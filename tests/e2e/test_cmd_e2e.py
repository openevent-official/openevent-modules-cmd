"""Installed CLI + installed SDK + real gRPC server; run with make e2e."""

from __future__ import annotations

import base64
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shlex
import signal
import socket
import subprocess
import sys
from threading import Lock
import time
import unittest

import grpc

from openevent.cmd_sdk import (
    CmdKillRequestInput, CmdOutputReadRequestInput, CmdRunRequestInput,
    create_client, parse_message,
)
import openevent.cmd_worker.cli
from openevent.sdk import AdminClient, OpenEventClient
from openevent.sdk.proto import openevent_pb2


def free_address():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return f"127.0.0.1:{sock.getsockname()[1]}"


def decoded(content):
    if content.content_encoding == "base64":
        return base64.b64decode(content.content)
    return content.content.encode("utf-8")


class CommitThenDropProxy(grpc.GenericRpcHandler):
    """Forward unary RPCs, losing only the first committed reply for each kind."""

    def __init__(self, target):
        self.upstream = grpc.insecure_channel(target)
        self.lock = Lock()
        self.calls = defaultdict(list)
        self.dropped = {}
        self.duplicates = set()
        self.lookups = set()
        self.server = grpc.server(ThreadPoolExecutor(max_workers=4))
        self.server.add_generic_rpc_handlers((self,))
        self.target = f"127.0.0.1:{self.server.add_insecure_port('127.0.0.1:0')}"
        self.server.start()

    def service(self, details):
        method = details.method

        def forward(raw, context):
            request = None
            kind = None
            if method.endswith("/GetSeqByUuid"):
                with self.lock:
                    self.lookups.add(openevent_pb2.GetSeqByUuidRequest.FromString(raw).uuid)
                context.abort(grpc.StatusCode.UNAVAILABLE, "test UUID lookup unavailable")
            if method.endswith("/PublishAutoSeq"):
                request = openevent_pb2.PublishAutoSeqRequest.FromString(raw)
                kind = json.loads(request.payload)["kind"]
                with self.lock:
                    self.calls[request.uuid].append(raw)
            try:
                response = self.upstream.unary_unary(method)(raw, timeout=5)
            except grpc.RpcError as exc:
                if request is not None and exc.code() == grpc.StatusCode.ALREADY_EXISTS:
                    with self.lock:
                        self.duplicates.add(request.uuid)
                context.abort(exc.code(), exc.details())
            if kind in {"cmd.run.accepted", "cmd.run.result"}:
                with self.lock:
                    drop = kind not in self.dropped
                    if drop:
                        self.dropped[kind] = request.uuid
                if drop:
                    context.abort(grpc.StatusCode.UNAVAILABLE, "test lost committed PublishAutoSeq reply")
            return response

        return grpc.unary_unary_rpc_method_handler(forward)

    def reconciled(self):
        with self.lock:
            return len(self.dropped) == 2 and set(self.dropped.values()) <= self.duplicates

    def close(self):
        self.server.stop(0).wait()
        self.upstream.close()


@unittest.skipUnless(os.environ.get("OPENEVENT_CMD_E2E_DIR"), "run make e2e")
class CmdEndToEndTest(unittest.TestCase):
    requester = 1001
    worker_principal = 30001

    def setUp(self):
        # An accidental source import would make wheel/entry-point checks useless.
        self.assertTrue(Path(openevent.cmd_worker.cli.__file__).resolve().is_relative_to(Path(sys.prefix)))
        self.root = Path(os.environ["OPENEVENT_CMD_E2E_DIR"]) / self._testMethodName
        self.root.mkdir(parents=True)
        self.worker = None
        self.worker_generation = 0
        self.public = free_address()
        admin_address = free_address()
        while admin_address == self.public:
            admin_address = free_address()
        server_config = self.root / "server.yaml"
        server_config.write_text(json.dumps({
            "grpc": {"listen_addr": self.public},
            "admin": {"listen_addr": admin_address},
            "storage": {"path": str(self.root / "server-data")},
            "limits": {"max_payload_bytes": 16384},
        }))
        self.server_log = self.root / "server.log"
        with self.server_log.open("wb") as log:
            self.server = subprocess.Popen(
                [os.environ["OPENEVENT_SERVER_BIN"], str(server_config)],
                stdout=log, stderr=log, cwd=self.root,
            )
        self.addCleanup(self.stop_server)
        for address in (self.public, admin_address):
            with grpc.insecure_channel(address) as channel:
                grpc.channel_ready_future(channel).result(timeout=10)
        self.admin = AdminClient(admin_address, timeout_ms=5000)
        self.addCleanup(self.admin.close)
        self.client = OpenEventClient(self.public, timeout_ms=5000)
        self.addCleanup(self.client.close)
        self.cmd = create_client(self.client)
        self.token = self.admin.add_token(self.requester).token
        self.worker_token = self.admin.add_token(self.worker_principal).token
        self.channel = self.create_channel("commands", "Plain text description")
        self.output = self.root / "output"
        self.addCleanup(self.stop_worker)

    def stop_server(self):
        if self.server.poll() is None:
            self.server.terminate()
            try:
                self.server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.server.kill()
                self.server.wait(timeout=5)
                self.fail(f"server failed to stop; see {self.server_log}")
        self.assertEqual(self.server.returncode, 0, str(self.server_log))

    def stop_worker(self):
        if self.worker is None:
            return
        process, self.worker = self.worker, None
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                self.fail(f"worker failed to stop; see {self.worker_log}")
        self.assertEqual(process.returncode, 128 + signal.SIGTERM, str(self.worker_log))

    def create_channel(self, name, description=""):
        return self.client.create_channel(
            self.requester, self.token, name,
            visibility=openevent_pb2.VISIBILITY_PRIVATE, protocol="cmd.v1",
            description=description, members=[self.worker_principal],
        ).channel.channel_id

    def start_worker(self, *, channels=None, target=None):
        self.assertIsNone(self.worker)
        self.worker_generation += 1
        config = self.root / "worker.yaml"
        config.write_text(json.dumps({
            "open_event": {
                "addr": target or self.public, "max_payload_bytes": 16384,
            },
            "principal": self.worker_principal, "token": self.worker_token,
            "channel_ids": channels or [self.channel], "output_dir": str(self.output),
            "max_concurrent_tasks": 2, "default_timeout_ms": 30000,
            "fetch_idle_sleep_ms": 10,
        }))
        self.worker_log = self.root / f"worker-{self.worker_generation}.log"
        with self.worker_log.open("wb") as log:
            self.worker = subprocess.Popen(
                [str(Path(sys.executable).parent / "cmd-worker"), "--config", str(config)],
                cwd=self.root, stdout=log, stderr=log,
            )

    def wait_for(self, check, label, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.assertIsNone(self.server.poll(), str(self.server_log))
            if self.worker is not None:
                self.assertIsNone(self.worker.poll(), str(self.worker_log))
            value = check()
            if value:
                return value
            time.sleep(0.02)
        self.fail(f"timed out waiting for {label}; artifacts: {self.root}")

    def messages(self):
        result = []
        cursor = 1
        last_seq = None
        while last_seq is None or cursor <= last_seq:
            response = self.client.fetch(self.requester, self.token, from_seq=cursor, limit=1000)
            if last_seq is None:
                last_seq = response.last_seq
            result.extend(parse_message(item) for item in response.messages if item.seq <= last_seq)
            self.assertGreater(response.next_seq, cursor)
            cursor = response.next_seq
        return result

    def replies(self, kind, prev_seq):
        return [item for item in self.messages()
                if item.payload.kind == kind and item.payload.prev_seq == prev_seq]

    def reply(self, kind, prev_seq):
        replies = self.wait_for(lambda: self.replies(kind, prev_seq), f"{kind} for {prev_seq}")
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0].principal, self.worker_principal)
        self.assertEqual(replies[0].recipients, (self.requester,))
        self.assertGreater(replies[0].seq, prev_seq)
        return replies[0]

    def run_command(self, command, *, channel=None, timeout_ms=30000):
        return self.cmd.publish_run_request(
            self.requester, self.token, channel or self.channel,
            CmdRunRequestInput(command, workdir=str(self.root), timeout_ms=timeout_ms),
        )

    def read_output(self, target, offset, nbytes, *, channel=None, stream="stdout"):
        seq = self.cmd.publish_output_read_request(
            self.requester, self.token, channel or self.channel,
            CmdOutputReadRequestInput(target, stream, offset, nbytes),
        )
        return self.reply("cmd.output.read.result", seq).payload

    def kill_command(self, target, *, channel=None):
        return self.cmd.publish_kill_request(
            self.requester, self.token, channel or self.channel, CmdKillRequestInput(target),
        )

    def test_run_accepted_result_and_output_read(self):
        self.start_worker()
        seq = self.run_command("printf 'hello world'; printf 'diagnostic' >&2")
        accepted = self.reply("cmd.run.accepted", seq)
        result = self.reply("cmd.run.result", seq)
        self.assertLess(accepted.seq, result.seq)
        self.assertEqual(result.payload.status, "SUCCESS")
        self.assertEqual(result.payload.exit_code, 0)
        self.assertEqual(decoded(result.payload.stdout), b"hello world")
        self.assertEqual(decoded(result.payload.stderr), b"diagnostic")
        self.assertEqual(result.payload.stdout.file, f"{self.channel}/{seq}.stdout")
        content = self.read_output(seq, 6, 5)
        self.assertEqual(content.status, "SUCCESS")
        self.assertEqual(decoded(content), b"world")
        self.assertEqual(content.bytes, 5)
        self.assertTrue(content.eof)

    def test_kill_running_command(self):
        self.start_worker()
        seq = self.run_command("printf started > started; exec sleep 30")
        self.reply("cmd.run.accepted", seq)
        self.wait_for(lambda: (self.root / "started").exists(), "command start")
        kill = self.kill_command(seq)
        self.assertEqual(self.reply("cmd.kill.result", kill).payload.status, "KILL_SENT")
        self.assertEqual(self.reply("cmd.run.result", seq).payload.status, "KILLED")

    def test_restart_recovers_accepted_without_executing_again(self):
        self.start_worker()
        seq = self.run_command("printf x >> executions; printf partial; exec sleep 30")
        self.reply("cmd.run.accepted", seq)
        marker = self.root / "executions"
        self.wait_for(lambda: marker.exists(), "first execution")
        self.wait_for(lambda: (self.output / str(self.channel) / f"{seq}.stdout").stat().st_size == 7,
                      "partial output")
        self.stop_worker()
        self.assertFalse(self.replies("cmd.run.result", seq))
        self.start_worker()
        recovered = self.reply("cmd.run.result", seq).payload
        self.assertEqual(recovered.status, "RECOVERY_FAILED")
        self.assertEqual(decoded(recovered.stdout), b"partial")
        followup = self.run_command("printf ready")
        self.assertEqual(self.reply("cmd.run.result", followup).payload.status, "SUCCESS")
        self.assertEqual(marker.read_bytes(), b"x")
        self.assertEqual(len(self.replies("cmd.run.accepted", seq)), 1)
        self.assertEqual(len(self.replies("cmd.run.result", seq)), 1)

    def test_timeout(self):
        self.start_worker()
        warmup = self.run_command("true")
        self.assertEqual(self.reply("cmd.run.result", warmup).payload.status, "SUCCESS")
        seq = self.run_command("exec sleep 30", timeout_ms=1000)
        self.reply("cmd.run.accepted", seq)
        result = self.reply("cmd.run.result", seq).payload
        self.assertEqual(result.status, "TIMEOUT")
        self.assertEqual(result.timeout_ms, 1000)
        self.assertIsNotNone(result.started_at_ms)

    def test_binary_output_is_read_in_bounded_chunks(self):
        self.start_worker()
        command = shlex.join([sys.executable, "-B", "-c", "import os; os.write(1, bytes(range(256)) * 256)"])
        seq = self.run_command(command)
        result = self.reply("cmd.run.result", seq).payload
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(result.stdout.bytes, 65536)
        self.assertIsNone(result.stdout.content)
        output = bytearray()
        while len(output) < result.stdout.bytes:
            part = self.read_output(seq, len(output), result.stdout.bytes)
            self.assertEqual(part.status, "SUCCESS")
            self.assertEqual(part.content_encoding, "base64")
            raw = decoded(part)
            self.assertEqual(part.bytes, len(raw))
            self.assertGreater(part.bytes, 0)
            self.assertLess(part.bytes, 16384)
            output.extend(raw)
            self.assertEqual(part.eof, len(output) == result.stdout.bytes)
        self.assertEqual(output, bytes(range(256)) * 256)
        after_end = self.read_output(seq, 65537, 1)
        self.assertEqual(after_end.status, "SUCCESS")
        self.assertEqual(decoded(after_end), b"")
        self.assertTrue(after_end.eof)

    def test_channel_scoping_for_requests_kill_and_output(self):
        second = self.create_channel("second")
        unowned = self.create_channel("unowned")
        ignored = self.run_command("printf unexpected > unowned-executed", channel=unowned)
        self.start_worker(channels=[self.channel, second])
        seq = self.run_command("printf started > started; printf output; exec sleep 30")
        self.reply("cmd.run.accepted", seq)
        self.wait_for(lambda: (self.root / "started").exists(), "command start")
        wrong_kill = self.kill_command(seq, channel=second)
        self.assertEqual(self.reply("cmd.kill.result", wrong_kill).payload.status, "NOT_FOUND")
        self.assertEqual(self.read_output(seq, 0, 20, channel=second).status, "NOT_FOUND")
        kill = self.kill_command(seq)
        self.assertEqual(self.reply("cmd.kill.result", kill).payload.status, "KILL_SENT")
        self.assertEqual(self.reply("cmd.run.result", seq).payload.status, "KILLED")
        self.assertFalse(self.replies("cmd.run.accepted", ignored))
        self.assertFalse(self.replies("cmd.run.result", ignored))
        self.assertFalse((self.root / "unowned-executed").exists())

    def test_lost_committed_replies_reconcile_same_uuid(self):
        proxy = CommitThenDropProxy(self.public)
        self.addCleanup(proxy.close)
        # Cleanup LIFO: worker must stop before its proxy closes.
        self.addCleanup(self.stop_worker)
        self.start_worker(target=proxy.target)
        seq = self.run_command("printf x >> executions; printf complete")
        accepted = self.reply("cmd.run.accepted", seq)
        result = self.reply("cmd.run.result", seq)
        self.assertEqual(result.payload.status, "SUCCESS")
        self.assertLess(accepted.seq, result.seq)
        self.wait_for(proxy.reconciled, "UUID reconciliation after both lost replies")
        with proxy.lock:
            self.assertEqual(set(proxy.dropped), {"cmd.run.accepted", "cmd.run.result"})
            self.assertFalse(proxy.lookups)
            for uuid in proxy.dropped.values():
                self.assertIn(uuid, proxy.duplicates)
                self.assertEqual(len(proxy.calls[uuid]), 2)
                self.assertEqual(proxy.calls[uuid][0], proxy.calls[uuid][1])
        self.assertEqual((self.root / "executions").read_bytes(), b"x")
        self.assertEqual(decoded(result.payload.stdout), b"complete")
        self.assertEqual(len(self.replies("cmd.run.accepted", seq)), 1)
        self.assertEqual(len(self.replies("cmd.run.result", seq)), 1)


if __name__ == "__main__":
    unittest.main()
