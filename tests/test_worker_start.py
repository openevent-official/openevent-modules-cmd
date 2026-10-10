from __future__ import annotations

import errno
import json
import os
import shlex
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from openevent.cmd_sdk.model import CmdRunRequest
from openevent.cmd_sdk.codec import dumps_payload
from openevent.sdk.proto import openevent_pb2
from openevent.cmd_worker.config import CmdWorkerConfig, OpenEventConfig
from openevent.cmd_worker.runner import now_ms
from openevent.cmd_worker.worker import CmdWorker, RunEvent

from test_worker_lifecycle import FakeClient, RecordingPublisher


class WorkerStartTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.config = CmdWorkerConfig(
            open_event=OpenEventConfig("unused"),
            principal=30001, token="token", channel_ids=(2001,),
            output_dir=self.root / "output", max_concurrent_tasks=1,
        )

    def test_unusable_workdir_returns_start_failed_and_allows_next_command(self):
        plain_file = self.root / "plain-file"
        plain_file.touch()
        name_limit = os.pathconf(self.root, "PC_NAME_MAX")
        for name, workdir in (
            ("missing", self.root / "missing"),
            ("not-directory", plain_file),
            ("too-long", self.root / ("x" * (name_limit + 1))),
        ):
            with self.subTest(case=name):
                self.assert_start_failure_then_success(workdir)

    def test_workdir_permission_error_returns_start_failed_and_allows_next_command(self):
        # Inject the OS failure so this also exercises permission handling when
        # the test process is root and can enter a chmod(0) directory.
        failure = PermissionError(errno.EACCES, "Permission denied", str(self.root))
        with_patch = patch("openevent.cmd_worker.runner.subprocess.Popen", side_effect=failure)
        self.assert_start_failure_then_success(self.root, with_patch)

    def test_nul_command_returns_start_failed_and_allows_next_command(self):
        self.assert_start_failure_then_success(self.root, command="printf hello\0world")

    def test_nul_workdir_returns_start_failed_and_allows_next_command(self):
        self.assert_start_failure_then_success(self.root / "nul\0directory")

    def test_request_object_keys_reach_each_child_without_changing_worker_environment(self):
        keys = [
            openevent_pb2.ObjectKey(object_id=2**63 + 7, object_token='token-"one"'),
            openevent_pb2.ObjectKey(object_id=23, object_token="token-two"),
        ]
        code = 'import os; print(os.environ["OPENEVENT_OBJECT_KEYS"])'
        child = f"{shlex.quote(sys.executable)} -B -c {shlex.quote(code)}"
        command = f"{child}; {child}"
        for recovered in (False, True):
            for attachments in (keys, []):
                with self.subTest(recovered=recovered, attachments=bool(attachments)):
                    timestamp = now_ms()
                    message = openevent_pb2.EventMessage(
                        seq=1, channel_id=2001, principal=1001, ts_ms=timestamp,
                        object_keys=attachments,
                        payload=dumps_payload({
                            "kind": "cmd.run.request", "command": command,
                            "ts_ms": timestamp, "timeout_ms": 30000,
                        }),
                    )
                    worker = CmdWorker(self.config, FakeClient([message] if recovered else []))
                    worker.publisher = RecordingPublisher()
                    self.addCleanup(worker.stop)
                    with patch.dict(os.environ, {"OPENEVENT_OBJECT_KEYS": "inherited-value"}):
                        if recovered:
                            worker.startup()
                        else:
                            worker._observe_realtime_message(message)
                        worker._drain_pending()
                        completion = worker.completions.get(timeout=5)
                        worker.completions.put(completion)
                        worker._drain_completions()
                        self.assertEqual(os.environ["OPENEVENT_OBJECT_KEYS"], "inherited-value")
                    result = worker.publisher.records[-1]
                    self.assertEqual(result["status"], "SUCCESS")
                    expected = [{"object_id": key.object_id, "object_token": key.object_token}
                                for key in attachments]
                    reads = [json.loads(line) for line in result["stdout"]["content"].splitlines()]
                    self.assertEqual(reads, [expected, expected])
                    worker.stop()

    def assert_start_failure_then_success(self, workdir, start_context=None, *, command="printf hello"):
        worker = CmdWorker(self.config, FakeClient())
        worker.publisher = RecordingPublisher()
        self.addCleanup(worker.stop)
        timestamp = now_ms()

        def schedule(seq, directory, command="printf hello"):
            request = CmdRunRequest(command=command, workdir=str(directory), timeout_ms=30000, ts_ms=timestamp)
            worker._handle_run_event(RunEvent(seq, 2001, 1001, timestamp, request))
            worker._drain_pending()

        with start_context if start_context is not None else nullcontext():
            schedule(1, workdir, command)

        result = worker.publisher.records[-1]
        self.assertEqual(result["status"], "START_FAILED")
        self.assertTrue(result["error_message"])
        self.assertNotIn("started_at_ms", result)
        self.assertNotIn("exit_code", result)
        self.assertFalse(worker.running)
        self.assertFalse(worker.pending)
        self.assertFalse(worker.starting_command)
        self.assertFalse(worker.stopping)

        schedule(2, self.root)
        completion = worker.completions.get(timeout=5)
        worker.completions.put(completion)
        worker._drain_completions()
        self.assertEqual(
            [(item["kind"], item.get("status")) for item in worker.publisher.records],
            [("cmd.run.accepted", None), ("cmd.run.result", "START_FAILED"),
             ("cmd.run.accepted", None), ("cmd.run.result", "SUCCESS")],
        )
        self.assertEqual(worker.publisher.records[-1]["stdout"]["content"], "hello")
        self.assertFalse(worker.running)
        self.assertFalse(worker.pending)


if __name__ == "__main__":
    unittest.main()
