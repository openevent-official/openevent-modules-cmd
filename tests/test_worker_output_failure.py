from __future__ import annotations

import errno
import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from openevent.cmd_sdk.model import CmdRunRequest
from openevent.cmd_worker.config import CmdWorkerConfig, OpenEventConfig
from openevent.cmd_worker.runner import now_ms
from openevent.cmd_worker.worker import CmdWorker, RunEvent

from test_worker_lifecycle import FakeClient, RecordingPublisher, message


class WorkerOutputFailureTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config = CmdWorkerConfig(
            open_event=OpenEventConfig("unused"),
            principal=30001, token="token", channel_ids=(2001,),
            output_dir=Path(directory.name), max_concurrent_tasks=2,
        )

    def fail_output_open(self, output_path):
        original_open = Path.open
        failure = OSError(errno.EIO, "injected output I/O error", str(output_path))

        def open_file(path, *args, **kwargs):
            if path == output_path:
                raise failure
            return original_open(path, *args, **kwargs)

        return patch.object(Path, "open", open_file)

    def test_result_read_failure_exits_without_success_and_cleans_up_other_command(self):
        worker = CmdWorker(self.config, FakeClient())
        worker.publisher = RecordingPublisher()
        self.addCleanup(worker.stop)
        timestamp = now_ms()
        for seq, command in ((1, "printf produced"), (2, "exec sleep 30")):
            request = CmdRunRequest(command=command, timeout_ms=30000, ts_ms=timestamp)
            worker._handle_run_event(RunEvent(seq, 2001, 1001, timestamp, request))
        worker._drain_pending()
        completed_task = worker.running[(2001, 1)]
        other_task = worker.running[(2001, 2)]
        completion = worker.completions.get(timeout=5)
        self.assertEqual(completion.seq, 1)
        self.assertIsNone(completion.error)
        worker.completions.put(completion)
        output_path = self.config.output_dir / str(completed_task.channel_id) / f"{completed_task.seq}.stdout"
        self.assertEqual(output_path.read_bytes(), b"produced")
        worker.startup = Mock()

        with self.fail_output_open(output_path):
            with self.assertRaises(OSError) as raised:
                worker.run()

        self.assertEqual(raised.exception.errno, errno.EIO)
        self.assertEqual([record["kind"] for record in worker.publisher.records],
                         ["cmd.run.accepted", "cmd.run.accepted"])
        self.assertFalse(worker._has_run_result(2001, 1))
        self.assertTrue(worker.stopping)
        self.assertEqual(completed_task.process.returncode, 0)
        self.assertEqual(other_task.process.returncode, -signal.SIGKILL)

    def test_recovery_read_failure_remains_unresolved_until_output_is_readable(self):
        history = [
            message(1, {"kind": "cmd.run.request", "command": "printf produced", "ts_ms": 1000}),
            message(2, {"kind": "cmd.run.accepted", "prev_seq": 1, "ts_ms": 1001}, 30001),
        ]
        output_path = self.config.output_dir / "2001/1.stdout"
        output_path.parent.mkdir()
        output_path.write_bytes(b"produced")

        for attempt in range(2):
            with self.subTest(attempt=attempt):
                worker = CmdWorker(self.config, FakeClient(history))
                worker.publisher = RecordingPublisher()
                worker.runner.start = Mock(side_effect=AssertionError("accepted command must not restart"))
                with self.fail_output_open(output_path):
                    with self.assertRaises(OSError) as raised:
                        worker.run()
                self.assertEqual(raised.exception.errno, errno.EIO)
                self.assertFalse(worker.publisher.records)
                self.assertFalse(worker._has_run_result(2001, 1))
                self.assertTrue(worker.stopping)
                worker.runner.start.assert_not_called()

        recovered = CmdWorker(self.config, FakeClient(history))
        recovered.publisher = RecordingPublisher()
        recovered.startup()
        self.assertEqual(len(recovered.publisher.records), 1)
        result = recovered.publisher.records[0]
        self.assertEqual(result["status"], "RECOVERY_FAILED")
        self.assertEqual(result["stdout"]["content"], "produced")
        self.assertEqual(result["stdout"]["bytes"], 8)
        self.assertTrue(recovered._has_run_result(2001, 1))


if __name__ == "__main__":
    unittest.main()
