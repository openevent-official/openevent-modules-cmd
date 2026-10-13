from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openevent.cmd_sdk import parse_message
from openevent.cmd_sdk.errors import CmdSDKError
from openevent.cmd_worker.config import CmdWorkerConfig, OpenEventConfig
from openevent.cmd_worker.worker import ActiveRun, CmdWorker

from test_worker_lifecycle import FakeClient, RecordingPublisher
from test_worker_system_messages import initialized_message, run_message


class WorkerParseFailureTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config = CmdWorkerConfig(
            open_event=OpenEventConfig("unused"),
            principal=30001, token="token", channel_ids=(2001,),
            output_dir=Path(directory.name),
        )

    def test_startup_exits_before_replaying_requests_when_history_cannot_be_parsed(self):
        self.check_parse_failures(realtime=False)

    def test_realtime_exits_and_cleans_running_commands_when_message_cannot_be_parsed(self):
        self.check_parse_failures(realtime=True)

    def check_parse_failures(self, *, realtime):
        cases = {
            "invalid_utf8": b"\xff",
            "invalid_json": b"{",
            "non_object_json": b"[]",
            "unknown_kind": {"kind": "unknown", "ts_ms": 1000},
            "system_kind_from_non_system_principal": {"kind": "system.member_added"},
            "invalid_run_request": {"kind": "cmd.run.request", "ts_ms": 1000},
            "invalid_kill_request": {"kind": "cmd.kill.request", "ts_ms": 1000, "target_seq": True},
            "invalid_output_request": {
                "kind": "cmd.output.read.request", "ts_ms": 1000,
                "target_seq": 2, "stream": "stdout", "offset": 0, "nbytes": 0,
            },
            "invalid_accepted": {"kind": "cmd.run.accepted", "ts_ms": 1000},
            "invalid_run_result": {
                "kind": "cmd.run.result", "ts_ms": 1000, "prev_seq": 2, "status": "SUCCESS",
            },
            "invalid_kill_result": {
                "kind": "cmd.kill.result", "ts_ms": 1000, "prev_seq": 2, "status": "NOT_RUNNING",
            },
            "invalid_output_result": {
                "kind": "cmd.output.read.result", "ts_ms": 1000, "prev_seq": 2, "status": "NOT_FOUND",
            },
        }
        for name, payload in cases.items():
            with self.subTest(name=name):
                malformed = SimpleNamespace(
                    seq=3, channel_id=2001, principal=1001, recipients=[],
                    object_keys=[], ts_ms=1000,
                    payload=payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8"),
                )
                client = FakeClient([initialized_message(1), run_message(2), malformed, run_message(4)])
                client.fetch = Mock(wraps=client.fetch)
                worker = CmdWorker(self.config, client)
                worker.publisher = RecordingPublisher()
                worker.runner.start = Mock()
                worker.runner.terminate = Mock()
                worker.runner.wait = Mock()
                if realtime:
                    worker.resolver.validate_startup()
                    worker.startup = Mock()
                    task = SimpleNamespace(channel_id=2001, seq=99)
                    worker.running[(2001, 99)] = ActiveRun(None, task)

                with (
                    patch("openevent.cmd_worker.worker.parse_message", wraps=parse_message) as parse,
                    self.assertRaisesRegex(RuntimeError, r"failed to parse Cmd message channel_id=2001 seq=3") as raised,
                ):
                    worker.run()

                self.assertIsInstance(raised.exception.__cause__, CmdSDKError)
                self.assertTrue(worker.stopping)
                self.assertEqual([call.args[0].seq for call in parse.call_args_list], [2, 3])
                client.fetch.assert_called_once()
                worker.runner.start.assert_not_called()
                self.assertFalse(worker.publisher.records)
                if realtime:
                    worker.runner.terminate.assert_called_once_with(task, "KILLED")
                    worker.runner.wait.assert_called_once_with(task)
                    self.assertEqual(list(worker.pending), [(2001, 2)])
                else:
                    self.assertFalse(worker.pending)
                    worker.runner.terminate.assert_not_called()
                    worker.runner.wait.assert_not_called()


if __name__ == "__main__":
    unittest.main()
