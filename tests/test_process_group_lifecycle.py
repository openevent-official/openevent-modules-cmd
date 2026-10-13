from __future__ import annotations

import ctypes
import errno
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openevent.cmd_sdk.model import CmdKillRequest, CmdRunRequest
from openevent.cmd_worker.config import CmdWorkerConfig, OpenEventConfig
from openevent.cmd_worker.runner import CommandRunner, CommandStartError, now_ms
from openevent.cmd_worker.worker import ActiveRun, CmdWorker, KillEvent, RunEvent


@unittest.skipUnless(sys.platform == "linux" and hasattr(os, "WNOWAIT"), "requires Linux waitid")
class ProcessGroupLifecycleTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.runner = CommandRunner(directory.name)
        self.tasks = []
        self.children = set()

        # Adopt only our orphaned descendants so these tests do not rely on PID 1
        # to reap the background sleeps after the shell leader exits.
        self.libc = ctypes.CDLL(None, use_errno=True)
        previous = ctypes.c_int()
        self._prctl(37, ctypes.byref(previous))  # PR_GET_CHILD_SUBREAPER
        self._prctl(36, 1)  # PR_SET_CHILD_SUBREAPER
        self.addCleanup(self._prctl, 36, previous.value)
        self.addCleanup(self._cleanup_processes)

    def _prctl(self, operation, argument):
        if self.libc.prctl(operation, argument, 0, 0, 0) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))

    def _start(self, command):
        task = self.runner.start(2001, len(self.tasks) + 1, command, None, 30000, now_ms() + 30000)
        self.tasks.append(task)
        output_path = self.runner.output_dir / str(task.channel_id) / f"{task.seq}.stdout"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            text = output_path.read_text().strip()
            if text:
                child = int(text)
                self.children.add(child)
                return task, child
            time.sleep(0.005)
        self.fail("shell did not report its background child")

    def _observe_leader_exit(self, task):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = os.waitid(os.P_PID, task.process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if status is not None:
                return status
            time.sleep(0.005)
        self.fail("shell leader did not exit")

    def _assert_child_running(self, child):
        state = Path(f"/proc/{child}/stat").read_text().rsplit(")", 1)[1].split()[0]
        self.assertNotIn(state, {"Z", "X"})

    def _reap_child(self, child):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            pid, status = os.waitpid(child, os.WNOHANG)
            if pid == child:
                self.children.remove(child)
                return status
            time.sleep(0.005)
        self.fail(f"background child {child} remained alive after group cleanup")

    def _cleanup_processes(self):
        for task in self.tasks:
            try:
                self.runner.terminate(task, "KILLED")
                self.runner.wait(task, timeout=5)
            except Exception:
                self._force_cleanup_waitable_leader(task)
                task.process.wait(timeout=5)
        for child in tuple(self.children):
            try:
                os.kill(child, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self._reap_child(child)

    def _force_cleanup_waitable_leader(self, task):
        # Also serialize fallback cleanup against the concurrent wait test. A
        # numeric PID alone is not proof that its original group still belongs
        # to this task after another thread has reaped the leader.
        with task.termination_lock:
            try:
                os.waitid(os.P_PID, task.process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            except ChildProcessError:
                return
            try:
                os.killpg(task.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def _assert_cached_calls_do_not_signal(self, task, return_code, killpg):
        call_count = killpg.call_count
        self.assertEqual(self.runner.poll(task), return_code)
        self.assertEqual(self.runner.wait(task, timeout=0), return_code)
        self.assertFalse(self.runner.terminate(task, "TIMEOUT"))
        self.assertEqual(self.runner.wait(task), return_code)
        self.assertEqual(killpg.call_count, call_count)

    def test_output_close_failure_cleans_group_before_start_failed(self):
        for stream in ("stdout", "stderr"):
            for leader_exits in (False, True):
                with self.subTest(stream=stream, leader_exits=leader_exits):
                    self._assert_start_failure_cleanup(close_stream=stream, leader_exits=leader_exits)

    def test_task_construction_base_exception_cleans_group_and_propagates(self):
        class ConstructionAborted(BaseException):
            pass

        self._assert_start_failure_cleanup(construction_error=ConstructionAborted("interrupted construction"))

    def test_popen_failure_does_not_signal_an_uncreated_process(self):
        with (
            patch("openevent.cmd_worker.runner.subprocess.Popen", side_effect=OSError(errno.EIO, "cannot start shell")),
            patch("openevent.cmd_worker.runner.os.killpg") as killpg,
        ):
            with self.assertRaisesRegex(CommandStartError, "cannot start shell"):
                self.runner.start(2001, 1, "true", None, 30000, now_ms() + 30000)
        killpg.assert_not_called()

    def _assert_start_failure_cleanup(self, *, close_stream=None, leader_exits=False, construction_error=None):
        worker = CmdWorker(CmdWorkerConfig(
            open_event=OpenEventConfig("127.0.0.1:9527"),
            principal=30001, token="token", channel_ids=(2001,),
            output_dir=self.runner.output_dir,
        ), object())
        worker.runner = self.runner
        original_open = Path.open
        original_popen = subprocess.Popen
        original_killpg = os.killpg
        processes = []
        reported_children = []
        failures = []
        output = self.runner.output_dir / "2001/1.stdout"
        command = 'sleep 30 & printf "%s\\n" "$!"; ' + ("exit 7" if leader_exits else "wait")

        def before_failure():
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with original_open(output, "r") as reader:
                    text = reader.read().strip()
                if text:
                    child = int(text)
                    self.children.add(child)
                    reported_children.append(child)
                    break
                time.sleep(0.005)
            else:
                self.fail("shell did not report its background child before startup failure")
            process = processes[0]
            self._assert_child_running(child)
            self.assertEqual(os.getpgid(child), process.pid)
            if leader_exits:
                self.assertEqual(self._observe_leader_exit(SimpleNamespace(process=process)).si_status, 7)
            else:
                self.assertIsNone(os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT))
            failures.append(True)

        class FailingOutput:
            def __init__(self, path, handle):
                self.path = path
                self.handle = handle

            def __enter__(self):
                return self.handle.__enter__()

            def __exit__(self, *args):
                result = self.handle.__exit__(*args)
                if self.path.suffix == "." + str(close_stream) and not failures:
                    before_failure()
                    raise OSError(errno.EIO, "injected output close failure", str(self.path))
                return result

        def open_output(path, *args, **kwargs):
            return FailingOutput(path, original_open(path, *args, **kwargs))

        def start_process(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            processes.append(process)
            return process

        def abort_construction(*args, **kwargs):
            before_failure()
            raise construction_error

        def inspect_then_signal(pgid, signum):
            process = processes[0]
            # The leader must not have been reaped before signalling its group.
            observed = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if leader_exits:
                self.assertEqual(observed.si_status, 7)
            self.assertEqual((pgid, signum), (process.pid, signal.SIGKILL))
            return original_killpg(pgid, signum)

        def assert_reaped():
            process = processes[0]
            self.assertEqual(process.returncode, 7 if leader_exits else -signal.SIGKILL)
            with self.assertRaises(ChildProcessError):
                os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            self.assertEqual(os.waitstatus_to_exitcode(self._reap_child(reported_children[0])), -signal.SIGKILL)

        def record_failure(channel_id, request_principal, result):
            self.assertEqual((channel_id, request_principal), (2001, 1001))
            self.assertEqual(result.status, "START_FAILED")
            self.assertIn("injected output close failure", result.error_message)
            assert_reaped()

        worker.publisher = SimpleNamespace(
            publish_run_accepted=Mock(), publish_run_result=Mock(side_effect=record_failure),
        )
        try:
            with (
                patch.object(Path, "open", open_output),
                patch("openevent.cmd_worker.runner.subprocess.Popen", side_effect=start_process),
                patch("openevent.cmd_worker.runner.os.killpg", side_effect=inspect_then_signal) as killpg,
            ):
                timestamp = now_ms()
                event = RunEvent(1, 2001, 1001, timestamp, CmdRunRequest(
                    command=command, timeout_ms=30000, ts_ms=timestamp,
                ))
                if construction_error is not None:
                    with patch("openevent.cmd_worker.runner.RunningCommand", side_effect=abort_construction):
                        with self.assertRaises(type(construction_error)) as raised:
                            worker._start_or_timeout(event)
                    self.assertIs(raised.exception, construction_error)
                    worker.publisher.publish_run_result.assert_not_called()
                    assert_reaped()
                else:
                    worker._start_or_timeout(event)
                    worker.publisher.publish_run_result.assert_called_once()
                killpg.assert_called_once()
                worker.publisher.publish_run_accepted.assert_called_once_with(2001, 1001, 1)
                self.assertEqual(len(failures), 1)
                self.assertFalse(worker.running)
                self.assertFalse(worker.starting_command)
        finally:
            for process in processes:
                try:
                    os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                except ChildProcessError:
                    continue
                try:
                    original_killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)

    def test_natural_exit_cleans_background_before_reaping_and_preserves_status(self):
        for exit_code, method in ((0, "poll"), (7, "wait")):
            with self.subTest(exit_code=exit_code, method=method):
                task, child = self._start(f'sleep 30 & printf "%s\\n" "$!"; exit {exit_code}')
                self.assertEqual(self._observe_leader_exit(task).si_status, exit_code)
                self._assert_child_running(child)
                self.assertEqual(os.getpgid(child), task.process.pid)
                real_killpg = os.killpg
                observed = []

                def inspect_then_signal(pgid, signum):
                    # WNOWAIT must still succeed here. Reaping the leader first
                    # would release its PID before signalling the old group ID.
                    status = os.waitid(os.P_PID, task.process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                    self.assertIsNotNone(status)
                    self.assertEqual(status.si_pid, task.process.pid)
                    self.assertEqual(status.si_status, exit_code)
                    observed.append((pgid, signum))
                    return real_killpg(pgid, signum)

                with patch("openevent.cmd_worker.runner.os.killpg", side_effect=inspect_then_signal) as killpg:
                    result = self.runner.poll(task) if method == "poll" else self.runner.wait(task, timeout=5)
                    self.assertEqual(result, exit_code)
                    self.assertEqual(task.process.returncode, exit_code)
                    self.assertIsNone(task.kill_reason)
                    self.assertEqual(observed, [(task.process.pid, signal.SIGKILL)])
                    self._assert_cached_calls_do_not_signal(task, exit_code, killpg)
                    self.assertIsNone(task.kill_reason)

                child_status = self._reap_child(child)
                self.assertEqual(os.waitstatus_to_exitcode(child_status), -signal.SIGKILL)

    def test_terminate_after_natural_exit_cleans_group_without_marking_killed(self):
        task, child = self._start('sleep 30 & printf "%s\\n" "$!"; exit 13')
        self._observe_leader_exit(task)
        self._assert_child_running(child)
        real_killpg = os.killpg
        with patch("openevent.cmd_worker.runner.os.killpg", wraps=real_killpg) as killpg:
            self.assertFalse(self.runner.terminate(task, "KILLED"))
            self.assertIsNone(task.kill_reason)
            self.assertEqual(self.runner.wait(task, timeout=5), 13)
            killpg.assert_called_once_with(task.process.pid, signal.SIGKILL)
            self._assert_cached_calls_do_not_signal(task, 13, killpg)
            self.assertIsNone(task.kill_reason)
        self.assertEqual(os.waitstatus_to_exitcode(self._reap_child(child)), -signal.SIGKILL)

    def test_interrupted_reap_uses_cached_status_without_signalling_old_group(self):
        class ReapInterrupted(Exception):
            pass

        task, child = self._start('sleep 30 & printf "%s\\n" "$!"; exit 23')
        self._observe_leader_exit(task)
        real_wait = task.process.wait
        real_killpg = os.killpg
        interrupted = False

        def reap_then_interrupt(*args, **kwargs):
            nonlocal interrupted
            result = real_wait(*args, **kwargs)
            if not interrupted:
                interrupted = True
                task.process.returncode = None
                raise ReapInterrupted("interrupted after real waitpid")
            return result

        with (
            patch.object(task.process, "wait", side_effect=reap_then_interrupt),
            patch("openevent.cmd_worker.runner.os.killpg", wraps=real_killpg) as killpg,
        ):
            with self.assertRaisesRegex(ReapInterrupted, "after real waitpid"):
                self.runner.poll(task)
            self.assertIsNone(task.process.returncode)
            with self.assertRaises(ChildProcessError):
                os.waitid(os.P_PID, task.process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            killpg.assert_called_once_with(task.process.pid, signal.SIGKILL)
            self._assert_cached_calls_do_not_signal(task, 23, killpg)
            self.assertEqual(task.process.returncode, 23)
            self.assertIsNone(task.kill_reason)
        self.assertEqual(os.waitstatus_to_exitcode(self._reap_child(child)), -signal.SIGKILL)

    def test_worker_kill_after_shell_exit_preserves_failure_and_reports_not_running(self):
        task, child = self._start('sleep 30 & printf "%s\\n" "$!"; exit 7')
        self._observe_leader_exit(task)
        self._assert_child_running(child)
        worker = CmdWorker(CmdWorkerConfig(
            open_event=OpenEventConfig("127.0.0.1:9527"),
            principal=30001, token="token", channel_ids=(2001,),
            output_dir=self.runner.output_dir,
        ), object())
        worker.runner = self.runner
        timestamp = now_ms()
        event = RunEvent(task.seq, 2001, 1001, timestamp, CmdRunRequest(
            command=task.process.args, timeout_ms=30000, ts_ms=timestamp,
        ))
        worker.running[(2001, task.seq)] = ActiveRun(event, task)
        records = []

        def record(channel_id, request_principal, result):
            records.append(result)
            return SimpleNamespace(seq=100 + len(records))

        worker.publisher = SimpleNamespace(publish_run_result=record, publish_kill_result=record)
        worker._handle_kill_event(KillEvent(
            10, 2001, 1001, CmdKillRequest(target_seq=task.seq, ts_ms=timestamp),
        ))

        self.assertEqual([result.status for result in records], ["FAILED", "NOT_RUNNING"])
        self.assertEqual(records[0].exit_code, 7)
        self.assertIsNone(task.kill_reason)
        self.assertFalse(worker.running)
        self.assertFalse(worker.pending)
        self.assertEqual(os.waitstatus_to_exitcode(self._reap_child(child)), -signal.SIGKILL)

    def test_kill_and_timeout_clean_group_and_preserve_first_reason(self):
        for reason, later_reason in (("KILLED", "TIMEOUT"), ("TIMEOUT", "KILLED")):
            with self.subTest(reason=reason):
                task, child = self._start('sleep 30 & printf "%s\\n" "$!"; wait')
                self._assert_child_running(child)
                real_killpg = os.killpg
                with patch("openevent.cmd_worker.runner.os.killpg", wraps=real_killpg) as killpg:
                    self.assertIsNone(self.runner.poll(task))
                    if reason == "TIMEOUT":
                        with self.assertRaises(subprocess.TimeoutExpired):
                            self.runner.wait(task, timeout=0.02)
                        killpg.assert_not_called()
                    self.assertTrue(self.runner.terminate(task, reason))
                    self.assertEqual(task.kill_reason, reason)
                    self.runner.terminate(task, later_reason)
                    self.assertEqual(self.runner.wait(task, timeout=5), -signal.SIGKILL)
                    self.assertEqual(task.kill_reason, reason)
                    self._assert_cached_calls_do_not_signal(task, -signal.SIGKILL, killpg)
                    self.assertEqual(task.kill_reason, reason)
                self.assertEqual(os.waitstatus_to_exitcode(self._reap_child(child)), -signal.SIGKILL)

    def test_worker_stop_cleans_both_exited_and_running_task_groups(self):
        exited, exited_child = self._start('sleep 30 & printf "%s\\n" "$!"; exit 0')
        self._observe_leader_exit(exited)
        running, running_child = self._start('sleep 30 & printf "%s\\n" "$!"; wait')
        worker = CmdWorker(CmdWorkerConfig(
            open_event=OpenEventConfig("127.0.0.1:9527"),
            principal=30001, token="token", channel_ids=(2001,),
            output_dir=self.runner.output_dir,
        ), object())
        worker.runner = self.runner
        for task in (exited, running):
            event = RunEvent(task.seq, task.channel_id, 1001, task.started_at_ms, CmdRunRequest(
                command=task.process.args, timeout_ms=30000, ts_ms=task.started_at_ms,
            ))
            worker.running[(task.channel_id, task.seq)] = ActiveRun(event, task)

        worker.stop()

        self.assertTrue(worker.stopping)
        self.assertEqual(exited.process.returncode, 0)
        self.assertIsNone(exited.kill_reason)
        self.assertEqual(running.process.returncode, -signal.SIGKILL)
        self.assertEqual(running.kill_reason, "KILLED")
        for child in (exited_child, running_child):
            self.assertEqual(os.waitstatus_to_exitcode(self._reap_child(child)), -signal.SIGKILL)

    def test_concurrent_wait_and_terminate_preserve_return_code_and_cleanup(self):
        task, child = self._start('sleep 30 & printf "%s\\n" "$!"; wait')
        observed_wait = threading.Event()
        results = Queue()
        real_waitid = os.waitid
        real_killpg = os.killpg

        def await_exit():
            try:
                results.put(("result", self.runner.wait(task, timeout=5)))
            except Exception as error:
                results.put(("error", error))

        waiter = threading.Thread(target=await_exit, daemon=True)

        def observe_wait(*args, **kwargs):
            result = real_waitid(*args, **kwargs)
            if threading.current_thread() is waiter:
                observed_wait.set()
            return result

        try:
            with (
                patch("openevent.cmd_worker.runner.os.waitid", side_effect=observe_wait),
                patch("openevent.cmd_worker.runner.os.killpg", wraps=real_killpg) as killpg,
            ):
                waiter.start()
                self.assertTrue(observed_wait.wait(timeout=2), "background wait did not observe the leader")
                self.assertTrue(self.runner.terminate(task, "KILLED"))
                waiter.join(timeout=5)
                self.assertFalse(waiter.is_alive(), "wait and terminate did not finish concurrently")
                self.assertEqual(results.get_nowait(), ("result", -signal.SIGKILL))
                self.assertEqual(task.kill_reason, "KILLED")
                self._assert_cached_calls_do_not_signal(task, -signal.SIGKILL, killpg)
            self.assertEqual(os.waitstatus_to_exitcode(self._reap_child(child)), -signal.SIGKILL)
        finally:
            if waiter.is_alive():
                self._force_cleanup_waitable_leader(task)
                waiter.join(timeout=6)


if __name__ == "__main__":
    unittest.main()
