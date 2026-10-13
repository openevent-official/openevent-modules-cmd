from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import grpc

from openevent.sdk import OpenEventClient
from openevent.sdk.proto import openevent_pb2
from openevent.cmd_sdk import (
    CmdKillRequestInput, CmdKillResultInput, CmdOutput,
    CmdOutputReadRequestInput, CmdOutputReadResultInput,
    CmdRunRequestInput, CmdRunResultInput, create_client, parse_message,
)
from openevent.cmd_sdk import codec
from openevent.cmd_sdk.model import CmdOutputReadRequest
from openevent.cmd_worker.config import CmdWorkerConfig, OpenEventConfig
from openevent.cmd_worker.publisher import PublishFatal, ReliablePublisher
from openevent.cmd_worker.worker import CmdWorker, OutputReadEvent


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
        raise RpcFailure(grpc.StatusCode.UNAVAILABLE)


def run_result():
    return CmdRunResultInput(
        prev_seq=10, status="SUCCESS", timeout_ms=1000,
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

    def output_worker(self, output_dir, client, max_payload_bytes=16 * 1024 * 1024):
        return CmdWorker(CmdWorkerConfig(
            open_event=OpenEventConfig("unused", max_payload_bytes=max_payload_bytes),
            principal=30001, token="worker-token", channel_ids=(2001,), output_dir=output_dir,
        ), client)

    def test_run_object_keys_round_trip_in_message_envelope(self):
        client, stub = self.client()
        keys = [
            openevent_pb2.ObjectKey(object_id=2**63 + 7, object_token='token-"one"'),
            openevent_pb2.ObjectKey(object_id=23, object_token="token-two"),
        ]
        create_client(client).publish_run_request(
            1001, "token", 2001, CmdRunRequestInput(command="true", ts_ms=123),
            object_keys=iter(keys), uuid=424242,
        )
        request, _ = stub.publishes[0]
        self.assertEqual(list(request.object_keys), keys)
        self.assertNotIn("object_keys", json.loads(request.payload))
        self.assertNotIn("object_token", json.loads(request.payload))
        message = openevent_pb2.EventMessage(
            seq=100, channel_id=request.channel_id, principal=request.principal,
            recipients=request.recipients, payload=request.payload, object_keys=request.object_keys,
        )
        parsed = parse_message(message)
        self.assertEqual(parsed.object_keys, tuple(keys))
        self.assertEqual(parsed.payload.command, "true")

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
                self.assertIsNone(published)
                allocate.assert_called_once_with()
                timestamp.assert_called_once_with()
                self.assertEqual(len(stub.publishes), 2)
                first, second = (request for request, _ in stub.publishes)
                self.assertEqual(first.SerializeToString(), second.SerializeToString())
                self.assertEqual(len(stub.committed), 1)
                self.assertEqual(stub.lookups, [])

    def test_all_worker_outputs_accept_committed_uuid_without_sequence_lookup(self):
        for kind, method, value in worker_outputs():
            with self.subTest(kind=kind):
                publisher, client, stub = self.publisher()
                stub.outcomes = ["commit_then_disconnect"]
                with patch.object(client, "get_uuid", wraps=client.get_uuid) as allocate:
                    published = getattr(publisher, method)(2001, 1001, value)
                self.assertIsNone(published)
                allocate.assert_called_once_with()
                self.assertEqual(len(stub.publishes), 2)
                self.assertEqual(len(stub.committed), 1)
                self.assertEqual(stub.lookups, [])
                first, second = (request for request, _ in stub.publishes)
                self.assertEqual(first.SerializeToString(), second.SerializeToString())
                self.assertEqual(json.loads(first.payload)["kind"], kind)

    def test_run_result_retry_keeps_its_generated_timestamp(self):
        publisher, client, stub = self.publisher()
        stub.outcomes = ["commit_then_disconnect"]
        result = replace(run_result(), ts_ms=None, started_at_ms=100)
        with patch("openevent.cmd_sdk.codec.now_ms", side_effect=[123, 456]) as timestamp:
            publisher.publish_run_result(2001, 1001, result)
        timestamp.assert_called_once_with()
        self.assertEqual(len(stub.publishes), 2)
        first, second = (request for request, _ in stub.publishes)
        self.assertEqual(first.SerializeToString(), second.SerializeToString())
        data = json.loads(first.payload)
        self.assertEqual(data["ts_ms"], 123)
        self.assertEqual(data["started_at_ms"], 100)
        self.assertNotIn("finished_at_ms", data)

    def test_worker_output_read_encodes_and_validates_once_including_retries(self):
        for raw, encoding in (("hello 世界".encode(), "utf-8"), (b"\x00\xff\x80", "base64")):
            for retry in (False, True):
                with self.subTest(encoding=encoding, retry=retry), tempfile.TemporaryDirectory() as tmp:
                    client, stub = self.client()
                    if retry:
                        stub.outcomes = ["commit_then_disconnect"]
                    root = Path(tmp)
                    (root / "2001").mkdir()
                    (root / "2001" / "10.stdout").write_bytes(raw)
                    worker = self.output_worker(root, client)
                    event = OutputReadEvent(12, 2001, 1001, CmdOutputReadRequest(
                        target_seq=10, stream="stdout", offset=0, nbytes=len(raw), ts_ms=100,
                    ))

                    with (
                        patch("openevent.cmd_worker.worker.now_ms", return_value=123),
                        patch("openevent.cmd_worker.publisher.dumps_payload", wraps=codec.dumps_payload) as encode,
                        patch.object(codec, "validate_payload_dict", wraps=codec.validate_payload_dict) as validate,
                        patch.object(codec.base64, "b64decode", wraps=codec.base64.b64decode) as decode,
                        patch.object(client, "publish_auto_seq", wraps=client.publish_auto_seq) as publish,
                        patch.object(client, "get_uuid", wraps=client.get_uuid) as allocate,
                    ):
                        worker._handle_output_read_event(event)

                    encode.assert_called_once()
                    validate.assert_called_once()
                    self.assertEqual(decode.call_count, 1 if encoding == "base64" else 0)
                    allocate.assert_called_once_with()
                    self.assertEqual(publish.call_count, 2 if retry else 1)
                    first_call = publish.call_args_list[0].kwargs
                    for call in publish.call_args_list[1:]:
                        self.assertIs(call.kwargs["payload"], first_call["payload"])
                        self.assertEqual(call.kwargs["uuid"], first_call["uuid"])
                    self.assertEqual(len(stub.committed), 1)
                    result = json.loads(stub.publishes[0][0].payload)
                    self.assertEqual(result["status"], "SUCCESS")
                    self.assertEqual(result["content_encoding"], encoding)
                    self.assertEqual(result["bytes"], len(raw))
                    self.assertEqual(result["ts_ms"], 123)
                    self.assertEqual(
                        result["content"].encode() if encoding == "utf-8" else codec.base64.b64decode(result["content"]),
                        raw,
                    )

    def test_oversized_output_read_publishes_empty_failure_with_original_metadata(self):
        for raw in (b"\x00" * 256, b"\xff" * 768):
            with self.subTest(raw_bytes=len(raw)), tempfile.TemporaryDirectory() as tmp:
                client, stub = self.client()
                stub.outcomes = ["commit_then_disconnect"]
                root = Path(tmp)
                (root / "2001").mkdir()
                (root / "2001" / "10.stdout").write_bytes(b"skip" + raw)
                worker = self.output_worker(root, client, max_payload_bytes=1024)
                event = OutputReadEvent(12, 2001, 1001, CmdOutputReadRequest(
                    target_seq=10, stream="stdout", offset=4, nbytes=len(raw), ts_ms=100,
                ))
                with (
                    patch("openevent.cmd_worker.worker.now_ms", return_value=123),
                    patch("openevent.cmd_worker.publisher.dumps_payload", wraps=codec.dumps_payload) as encode,
                    patch.object(client, "publish_auto_seq", wraps=client.publish_auto_seq) as publish,
                    patch.object(client, "get_uuid", wraps=client.get_uuid) as allocate,
                ):
                    worker._handle_output_read_event(event)

                self.assertEqual(encode.call_count, 2)
                allocate.assert_called_once_with()
                self.assertEqual(publish.call_count, 2)
                first, second = publish.call_args_list
                self.assertIs(first.kwargs["payload"], second.kwargs["payload"])
                self.assertEqual(first.kwargs["uuid"], second.kwargs["uuid"])
                self.assertEqual(len(stub.committed), 1)
                request, _ = stub.publishes[0]
                self.assertLessEqual(len(request.payload), 1024)
                self.assertEqual(json.loads(request.payload), {
                    "kind": "cmd.output.read.result", "prev_seq": 12, "target_seq": 10,
                    "stream": "stdout", "offset": 4, "nbytes": len(raw), "ts_ms": 123,
                    "status": "READ_FAILED", "bytes": 0, "eof": False,
                    "content_encoding": "utf-8", "content": "",
                    "error_message": "encoded output read result exceeds max_payload_bytes",
                })

    def test_output_read_failure_that_cannot_fit_is_fatal_before_uuid_allocation(self):
        publisher, client, stub = self.publisher(max_payload_bytes=100)
        with patch.object(client, "get_uuid", wraps=client.get_uuid) as allocate:
            with self.assertRaisesRegex(PublishFatal, "payload exceeds max_payload_bytes"):
                publisher.publish_output_read_result(2001, 1001, read_result())
        allocate.assert_not_called()
        self.assertEqual(stub.allocations, [])
        self.assertEqual(stub.publishes, [])

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
