from __future__ import annotations

import os
import signal
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openevent.cmd_sdk.model import CmdRunRequest
from openevent.cmd_worker import cli
from openevent.cmd_worker.config import CmdWorkerConfig, OpenEventConfig
from openevent.cmd_worker.runner import now_ms
from openevent.cmd_worker.worker import ActiveRun, CmdWorker, RunEvent


class WorkerSignalsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config = CmdWorkerConfig(
            open_event=OpenEventConfig("127.0.0.1:9527"),
            principal=30001, token="token", channel_ids=(2001,),
            output_dir=Path(directory.name), max_concurrent_tasks=2,
        )

    def worker(self):
        client = SimpleNamespace(fetch=Mock(), close=Mock())
        worker = CmdWorker(self.config, client)
        worker.publisher = SimpleNamespace(
            publish_run_accepted=Mock(return_value=SimpleNamespace(seq=100)),
        )
        return worker, client

    def run_cli(self, worker, client):
        previous = {value: signal.getsignal(value) for value in (signal.SIGTERM, signal.SIGINT)}
        try:
            with (
                patch.object(cli, "load_config", return_value=self.config),
                patch.object(cli, "CmdWorker", return_value=worker),
                patch("openevent.sdk.OpenEventClient", return_value=client),
            ):
                return cli.main(["--config", "unused-test-config.yaml"])
        finally:
            restored = {value: signal.getsignal(value) for value in previous}
            # Restore the test process even if a regression bypasses CLI cleanup.
            for value, handler in previous.items():
                signal.signal(value, handler)
            self.assertEqual(restored, previous)

    def test_sigterm_during_fetch_stops_without_retrying(self):
        worker, client = self.worker()
        worker.startup = Mock()

        def interrupted_fetch(*args, **kwargs):
            os.kill(os.getpid(), signal.SIGTERM)
            self.fail("SIGTERM outside process creation must interrupt Fetch")

        client.fetch.side_effect = interrupted_fetch
        with (
            patch("openevent.cmd_worker.worker.time.sleep") as sleep,
            patch.object(worker, "stop", wraps=worker.stop) as stop,
        ):
            status = self.run_cli(worker, client)

        self.assertEqual(status, 143)
        client.fetch.assert_called_once()
        sleep.assert_not_called()
        stop.assert_called_once_with()
        client.close.assert_called_once_with()
        self.assertTrue(worker.stopping)

    def test_non_rpc_fetch_exception_is_not_retried(self):
        worker, client = self.worker()
        worker.startup = Mock()
        client.fetch.side_effect = ValueError("invalid SDK state")
        with (
            patch("openevent.cmd_worker.worker.time.sleep") as sleep,
            patch.object(worker, "stop", wraps=worker.stop) as stop,
        ):
            with self.assertRaisesRegex(ValueError, "invalid SDK state"):
                self.run_cli(worker, client)

        client.fetch.assert_called_once()
        sleep.assert_not_called()
        stop.assert_called_once_with()
        client.close.assert_called_once_with()
        self.assertTrue(worker.stopping)

    def test_signals_during_cleanup_finish_all_tasks_and_preserve_exit_cause(self):
        for first_signal in (signal.SIGTERM, signal.SIGINT, None):
            with self.subTest(first_signal=first_signal):
                worker, client = self.worker()
                created = []
                original_terminate = worker.runner.terminate
                original_wait = worker.runner.wait

                def startup():
                    for seq in (1, 2):
                        task = worker.runner.start(
                            2001, seq, "exec sleep 30", None, 30000, now_ms() + 30000,
                        )
                        created.append(task)
                        event = RunEvent(seq, 2001, 1001, task.started_at_ms, CmdRunRequest(
                            command="exec sleep 30", timeout_ms=30000, ts_ms=task.started_at_ms,
                        ))
                        worker.running[(2001, seq)] = ActiveRun(event, task)

                def interrupted_fetch(*args, **kwargs):
                    if first_signal is None:
                        raise ValueError("invalid SDK state")
                    os.kill(os.getpid(), first_signal)
                    self.fail("the first stop signal must interrupt Fetch")

                def terminate_then_signal(task, reason):
                    result = original_terminate(task, reason)
                    # Interrupt between tasks, including when cleanup began
                    # because of a program error rather than a stop signal.
                    os.kill(os.getpid(), signal.SIGINT)
                    os.kill(os.getpid(), signal.SIGTERM)
                    return result

                def wait_then_signal(task):
                    result = original_wait(task, timeout=5)
                    os.kill(os.getpid(), signal.SIGTERM)
                    return result

                worker.startup = startup
                client.fetch.side_effect = interrupted_fetch
                try:
                    with (
                        patch.object(worker.runner, "terminate", side_effect=terminate_then_signal) as terminate,
                        patch.object(worker.runner, "wait", side_effect=wait_then_signal) as wait,
                    ):
                        if first_signal is None:
                            with self.assertRaisesRegex(ValueError, "invalid SDK state"):
                                self.run_cli(worker, client)
                        else:
                            self.assertEqual(self.run_cli(worker, client), 128 + first_signal)

                    self.assertEqual(terminate.call_count, 2)
                    self.assertEqual(wait.call_count, 2)
                    self.assertEqual(len(created), 2)
                    for task in created:
                        self.assertEqual(task.process.returncode, -signal.SIGKILL)
                        with self.assertRaises(ChildProcessError):
                            os.waitid(os.P_PID, task.process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                    client.fetch.assert_called_once()
                    client.close.assert_called_once_with()
                finally:
                    for task in created:
                        original_terminate(task, "KILLED")
                        original_wait(task, timeout=5)

    def test_stop_signal_during_process_creation_registers_and_kills_child(self):
        for stop_signal, expected_status in ((signal.SIGTERM, 143), (signal.SIGINT, 130)):
            with self.subTest(signal=stop_signal):
                worker, client = self.worker()
                created = []
                observed_at_signal = []
                tracked_at_stop = []
                original_start = worker.runner.start
                original_stop = worker.stop

                def startup():
                    timestamp = now_ms()
                    for seq in (1, 2):
                        event = RunEvent(
                            seq, 2001, 1001, timestamp,
                            CmdRunRequest(command="exec sleep 30", timeout_ms=30000, ts_ms=timestamp),
                        )
                        worker._handle_run_event(event)
                    worker._drain_pending()

                def start_then_signal(*args, **kwargs):
                    task = original_start(*args, **kwargs)
                    created.append(task)
                    observed_at_signal.append((worker.starting_command, (2001, 1) in worker.running))
                    os.kill(os.getpid(), stop_signal)
                    later_signal = signal.SIGINT if stop_signal == signal.SIGTERM else signal.SIGTERM
                    os.kill(os.getpid(), later_signal)
                    return task

                def record_stop():
                    tracked_at_stop.extend(active.command for active in worker.running.values())
                    original_stop()

                worker.startup = startup
                try:
                    with (
                        patch.object(worker.runner, "start", side_effect=start_then_signal) as start,
                        patch.object(worker, "stop", side_effect=record_stop) as stop,
                    ):
                        status = self.run_cli(worker, client)

                    self.assertEqual(status, expected_status)
                    self.assertEqual(observed_at_signal, [(True, False)])
                    start.assert_called_once()
                    stop.assert_called_once_with()
                    self.assertEqual(len(created), 1)
                    self.assertEqual(tracked_at_stop, created)
                    self.assertIs(worker.running[(2001, 1)].command, created[0])
                    self.assertEqual(worker.runner.wait(created[0], timeout=5), -signal.SIGKILL)
                    self.assertEqual([event.seq for event in worker.pending.values()], [2])
                    worker.publisher.publish_run_accepted.assert_called_once_with(2001, 1001, 1)
                    client.fetch.assert_not_called()
                    client.close.assert_called_once_with()
                    self.assertTrue(worker.stopping)
                    self.assertFalse(worker.starting_command)
                finally:
                    for task in created:
                        worker.runner.terminate(task, "KILLED")
                        worker.runner.wait(task, timeout=5)


if __name__ == "__main__":
    unittest.main()
