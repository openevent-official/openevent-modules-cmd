from __future__ import annotations

import errno
import os
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from openevent.cmd_sdk.model import CmdRunRequest
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
        self.assertFalse(worker.requests)
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
        self.assertFalse(worker.requests)


if __name__ == "__main__":
    unittest.main()
