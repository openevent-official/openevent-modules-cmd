from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import grpc

from openevent.sdk import OpenEventClient
from openevent.cmd_sdk import (
    CmdKillRequestInput, CmdKillResultInput, CmdOutput,
    CmdOutputReadRequestInput, CmdOutputReadResultInput,
    CmdRunRequestInput, CmdRunResultInput, create_client,
)
from openevent.cmd_worker.publisher import PublishFatal, ReliablePublisher


class RpcFailure(grpc.RpcError):
    def __init__(self, status):
        self.status = status

    def code(self):
        return self.status


class EventStub:
    """Exercise the installed SDK's protobuf request construction without a server."""

    def __init__(self):
        self.allocations = []
        self.publishes = []
        self.lookups = []
        self.outcomes = []
        self.lookup_error = None
        self.committed = {}

    def AllocateUuids(self, request, *, timeout):
        self.allocations.append((request, timeout))
        start = 7000 + sum(item.count for item, _ in self.allocations[:-1])
        return SimpleNamespace(uuids=list(range(start, start + request.count)))

    def PublishAutoSeq(self, request, *, timeout):
        self.publishes.append((request, timeout))
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, Exception):
            raise outcome
        if request.uuid in self.committed:
            raise RpcFailure(grpc.StatusCode.ALREADY_EXISTS)
        seq = 100 + len(self.committed)
        self.committed[request.uuid] = (seq, request.payload)
        if outcome == "commit_then_disconnect":
            raise RpcFailure(grpc.StatusCode.UNAVAILABLE)
        return SimpleNamespace(seq=seq)

    def GetSeqByUuid(self, request, *, timeout):
        self.lookups.append((request, timeout))
        if self.lookup_error is not None:
            raise self.lookup_error
        return SimpleNamespace(seq=self.committed[request.uuid][0])


def run_result():
    return CmdRunResultInput(
        prev_seq=10, status="SUCCESS", timeout_ms=1000, finished_at_ms=123,
        exit_code=0, ts_ms=123,
        stdout=CmdOutput(file="2001/10.stdout", bytes=0, content_encoding="utf-8", content=""),
        stderr=CmdOutput(file="2001/10.stderr", bytes=0, content_encoding="utf-8", content=""),
    )


def read_result():
    return CmdOutputReadResultInput(
        prev_seq=12, target_seq=10, stream="stdout", offset=0, nbytes=1,
        status="SUCCESS", bytes=1, eof=True, content_encoding="utf-8", content="x", ts_ms=123,
    )


def worker_outputs():
    return (
        ("cmd.run.accepted", "publish_run_accepted", 10),
        ("cmd.run.result", "publish_run_result", run_result()),
        ("cmd.kill.result", "publish_kill_result",
         CmdKillResultInput(prev_seq=11, target_seq=10, status="KILL_SENT", ts_ms=123)),
        ("cmd.output.read.result", "publish_output_read_result", read_result()),
    )


class PublishTest(unittest.TestCase):
    def client(self):
        client = OpenEventClient("127.0.0.1:1")
        self.addCleanup(client.close)
        stub = EventStub()
        client.event_stub = stub
        return client, stub

    def publisher(self, max_payload_bytes=16 * 1024 * 1024):
        client, stub = self.client()
        publisher = ReliablePublisher(client, 30001, "worker-token", max_payload_bytes)
        return publisher, client, stub

    def test_all_public_publish_helpers_use_installed_sdk_and_uuid(self):
        cases = (
            ("cmd.run.request", "publish_run_request", (CmdRunRequestInput(command="true", ts_ms=123),), []),
            ("cmd.kill.request", "publish_kill_request", (CmdKillRequestInput(target_seq=10, ts_ms=123),), []),
            ("cmd.output.read.request", "publish_output_read_request",
             (CmdOutputReadRequestInput(target_seq=10, stream="stdout", offset=0, nbytes=1, ts_ms=123),), []),
        ) + tuple((kind, method, (1001, value), [1001]) for kind, method, value in worker_outputs())
        for explicit in (False, True):
            for kind, method, args, recipients in cases:
                with self.subTest(kind=kind, explicit_uuid=explicit):
                    client, stub = self.client()
                    protocol = create_client(client)
                    with patch.object(client, "get_uuid", wraps=client.get_uuid) as allocate:
                        options = {"uuid": 424242} if explicit else {}
                        seq = getattr(protocol, method)(30001, "worker-token", 2001, *args, **options)
                    self.assertEqual(seq, 100)
                    if explicit:
                        allocate.assert_not_called()
                        self.assertEqual(stub.allocations, [])
                    else:
                        allocate.assert_called_once_with()
                        self.assertEqual(len(stub.allocations), 1)
                    self.assertEqual(len(stub.publishes), 1)
                    request, timeout = stub.publishes[0]
                    self.assertEqual(request.uuid, 424242 if explicit else 7000)
                    self.assertEqual((request.principal, request.token, request.channel_id),
                                     (30001, "worker-token", 2001))
                    self.assertEqual(list(request.recipients), recipients)
                    self.assertEqual(json.loads(request.payload)["kind"], kind)
                    self.assertEqual(timeout, 30.0)

    def test_uncertain_publish_retries_same_uuid_and_payload(self):
        for status in (
            grpc.StatusCode.CANCELLED, grpc.StatusCode.DEADLINE_EXCEEDED,
            grpc.StatusCode.UNKNOWN, grpc.StatusCode.UNAVAILABLE,
            grpc.StatusCode.INTERNAL, grpc.StatusCode.DATA_LOSS,
            grpc.StatusCode.FAILED_PRECONDITION, grpc.StatusCode.OUT_OF_RANGE,
            grpc.StatusCode.UNIMPLEMENTED,
        ):
            with self.subTest(status=status):
                publisher, client, stub = self.publisher()
                stub.outcomes = [RpcFailure(status)]
                with patch.object(client, "get_uuid", wraps=client.get_uuid) as allocate:
                    with patch("openevent.cmd_sdk.codec.now_ms", side_effect=[123, 456]) as timestamp:
                        published = publisher.publish_run_accepted(2001, 1001, 10)
                self.assertEqual(published.seq, 100)
                allocate.assert_called_once_with()
                timestamp.assert_called_once_with()
                self.assertEqual(len(stub.publishes), 2)
                first, second = (request for request, _ in stub.publishes)
                self.assertEqual(first.SerializeToString(), second.SerializeToString())
                self.assertEqual(len(stub.committed), 1)
                self.assertEqual(stub.lookups, [])

    def test_committed_publish_with_lost_response_resolves_existing_uuid(self):
        publisher, client, stub = self.publisher()
        stub.outcomes = ["commit_then_disconnect"]
        with patch.object(client, "get_uuid", wraps=client.get_uuid) as allocate:
            published = publisher.publish_run_accepted(2001, 1001, 10)
        allocate.assert_called_once_with()
        self.assertEqual(published.seq, 100)
        self.assertEqual(len(stub.publishes), 2)
        self.assertEqual(len(stub.committed), 1)
        self.assertEqual(len(stub.lookups), 1)
        first, second = (request for request, _ in stub.publishes)
        self.assertEqual(first.SerializeToString(), second.SerializeToString())
        self.assertEqual(stub.lookups[0][0].uuid, first.uuid)

    def test_already_exists_queries_committed_sequence_without_retry(self):
        publisher, _, stub = self.publisher()
        stub.committed[7000] = (89, b"already committed")
        published = publisher.publish_run_accepted(2001, 1001, 10)
        self.assertEqual(published.seq, 89)
        self.assertEqual(len(stub.publishes), 1)
        self.assertEqual([request.uuid for request, _ in stub.lookups], [7000])

    def test_definite_publish_errors_are_not_retried(self):
        for status in (
            grpc.StatusCode.INVALID_ARGUMENT, grpc.StatusCode.UNAUTHENTICATED,
            grpc.StatusCode.PERMISSION_DENIED, grpc.StatusCode.NOT_FOUND,
            grpc.StatusCode.RESOURCE_EXHAUSTED, grpc.StatusCode.ABORTED,
        ):
            with self.subTest(status=status):
                publisher, client, stub = self.publisher()
                failure = RpcFailure(status)
                stub.outcomes = [failure]
                with patch.object(client, "get_uuid", wraps=client.get_uuid) as allocate:
                    with self.assertRaises(PublishFatal) as raised:
                        publisher.publish_run_accepted(2001, 1001, 10)
                self.assertIs(raised.exception.__cause__, failure)
                allocate.assert_called_once_with()
                self.assertEqual(len(stub.publishes), 1)
                self.assertEqual(stub.lookups, [])

    def test_uuid_lookup_failure_is_fatal(self):
        publisher, _, stub = self.publisher()
        stub.outcomes = [RpcFailure(grpc.StatusCode.ALREADY_EXISTS)]
        stub.lookup_error = RpcFailure(grpc.StatusCode.UNAVAILABLE)
        with self.assertRaises(PublishFatal) as raised:
            publisher.publish_run_accepted(2001, 1001, 10)
        self.assertIs(raised.exception.__cause__, stub.lookup_error)
        self.assertEqual(len(stub.publishes), 1)
        self.assertEqual(len(stub.lookups), 1)

    def test_two_uncertain_failures_are_fatal(self):
        publisher, client, stub = self.publisher()
        failures = [RpcFailure(grpc.StatusCode.UNAVAILABLE), RpcFailure(grpc.StatusCode.DEADLINE_EXCEEDED)]
        stub.outcomes = list(failures)
        with patch.object(client, "get_uuid", wraps=client.get_uuid) as allocate:
            with self.assertRaises(PublishFatal) as raised:
                publisher.publish_run_accepted(2001, 1001, 10)
        allocate.assert_called_once_with()
        self.assertIs(raised.exception.__cause__, failures[-1])
        self.assertEqual(len(stub.publishes), 2)
        first, second = (request for request, _ in stub.publishes)
        self.assertEqual(first.SerializeToString(), second.SerializeToString())
        self.assertEqual(stub.lookups, [])

    def test_all_worker_outputs_enforce_budget_before_allocating_uuid(self):
        for kind, method, value in worker_outputs():
            with self.subTest(kind=kind):
                publisher, client, stub = self.publisher()
                with patch("openevent.cmd_sdk.codec.now_ms", return_value=123):
                    getattr(publisher, method)(2001, 1001, value)
                    payload_bytes = len(stub.publishes[-1][0].payload)
                    publisher.max_payload_bytes = payload_bytes
                    getattr(publisher, method)(2001, 1001, value)
                    publisher.max_payload_bytes = payload_bytes - 1
                    with patch.object(client, "get_uuid", wraps=client.get_uuid) as allocate:
                        with self.assertRaisesRegex(PublishFatal, "payload exceeds max_payload_bytes"):
                            getattr(publisher, method)(2001, 1001, value)
                allocate.assert_not_called()
                self.assertEqual(len(stub.publishes), 2)


if __name__ == "__main__":
    unittest.main()
