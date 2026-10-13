from __future__ import annotations

import subprocess
import tempfile
import unittest
import weakref
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openevent.cmd_sdk.codec import (
    dumps_payload,
    kill_result_input_to_dict,
    output_read_result_input_to_dict,
    run_accepted_to_dict,
    run_result_input_to_dict,
)
from openevent.cmd_sdk.model import CmdKillRequest, CmdOutputReadRequest, CmdRunRequest
from openevent.cmd_worker.config import CmdWorkerConfig, OpenEventConfig
from openevent.cmd_worker.runner import RunningCommand
from openevent.cmd_worker.worker import ActiveRun, CmdWorker, CompletionEvent, KillEvent, OutputReadEvent, RunEvent
from openevent.sdk.proto import openevent_pb2


def message(seq: int, payload: dict, principal: int = 1001):
    return SimpleNamespace(
        seq=seq,
        channel_id=2001,
        principal=principal,
        recipients=[] if principal == 1001 else [1001],
        object_keys=[],
        ts_ms=1000,
        payload=dumps_payload(payload),
    )


class FakeClient:
    def __init__(self, history=()):
        self.history = list(history)

    def get_channel(self, principal, token, channel_id):
        return SimpleNamespace(channel=SimpleNamespace(
            protocol="cmd.v3",
            visibility=openevent_pb2.VISIBILITY_PRIVATE,
            members=[principal],
            description="Command execution",
        ))

    def fetch(self, principal, token, *, from_seq, **kwargs):
        last_seq = max((item.seq for item in self.history), default=0)
        return SimpleNamespace(
            messages=[item for item in self.history if item.seq >= from_seq],
            next_seq=last_seq + 1,
            last_seq=last_seq,
        )


class RecordingPublisher:
    def __init__(self, next_seq=100):
        self.next_seq = next_seq
        self.records = []
        self.messages = []
        self.fail_kill_result = False
        self.on_accepted = lambda: None

    def _publish(self, data):
        seq = self.next_seq
        self.next_seq += 1
        self.records.append(data)
        self.messages.append(message(seq, data, principal=30001))
        return SimpleNamespace(seq=seq)

    def publish_run_accepted(self, channel_id, request_principal, prev_seq):
        self.on_accepted()
        return self._publish(run_accepted_to_dict(prev_seq))

    def publish_run_result(self, channel_id, request_principal, result):
        return self._publish(run_result_input_to_dict(result))

    def publish_kill_result(self, channel_id, request_principal, result):
        if self.fail_kill_result:
            raise RuntimeError("worker crashed before kill result was persisted")
        return self._publish(kill_result_input_to_dict(result))

    def publish_output_read_result(self, channel_id, request_principal, result):
        return self._publish(output_read_result_input_to_dict(result))


class FakeProcess:
    def __init__(self, returncode=None):
        self.returncode = returncode

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            raise AssertionError("test process has not exited")
        return self.returncode


class WorkerLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.output_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.output_dir.cleanup)
        self.config = CmdWorkerConfig(
            open_event=OpenEventConfig(addr="127.0.0.1:9527"),
            principal=30001,
            token="token",
            channel_ids=(2001,),
            output_dir=Path(self.output_dir.name),
            max_concurrent_tasks=1,
        )

    def worker(self, history=()):
        worker = CmdWorker(self.config, FakeClient(history))
        worker.publisher = RecordingPublisher(next_seq=max((item.seq for item in history), default=99) + 1)
        worker.runner.poll = Mock(side_effect=lambda task: task.process.returncode)
        worker.runner.wait = Mock(side_effect=lambda task, timeout=None: task.process.wait(timeout=timeout))

        def terminate(task, reason):
            if task.process.returncode is not None:
                return False
            task.kill_reason = task.kill_reason or reason
            task.process.returncode = -9
            return True

        worker.runner.terminate = Mock(side_effect=terminate)
        return worker

    def run_event(self, seq=1, channel_id=2001):
        return RunEvent(seq, channel_id, 1001, 1000, CmdRunRequest(command="printf hello", ts_ms=1000, timeout_ms=1000))

    def kill_event(self, seq=2):
        return KillEvent(seq, 2001, 1001, CmdKillRequest(target_seq=1, ts_ms=1001))

    def running_task(self, worker, returncode=None, seq=1):
        event = self.run_event(seq=seq)
        task = RunningCommand(
            channel_id=2001,
            seq=event.seq,
            process=FakeProcess(returncode),
            started_at_ms=1100,
            timeout_ms=1000,
            deadline_ms=2000,
        )
        worker.running[(2001, event.seq)] = ActiveRun(event, task)
        return task

    def test_pending_cancel_persists_target_result_before_kill_result(self):
        worker = self.worker()
        worker._handle_run_event(self.run_event())
        worker._handle_kill_event(self.kill_event())

        self.assertEqual(
            [(item["kind"], item["status"]) for item in worker.publisher.records],
            [("cmd.run.result", "KILLED"), ("cmd.kill.result", "REMOVED_PENDING")],
        )
        self.assertFalse(worker.pending)
        self.assertFalse(worker.running)

    def test_pending_cancel_preserves_channel_scope_and_remaining_fifo_order(self):
        self.config = replace(self.config, channel_ids=(2001, 2002))
        worker = self.worker()
        events = [self.run_event(seq, channel_id) for seq, channel_id in
                  ((1, 2001), (2, 2002), (3, 2001), (4, 2002))]
        for event in events:
            worker._handle_run_event(event)

        # The same target exists only in channel 2002. An earlier kill in
        # channel 2001 must leave every queued request in place.
        request = CmdKillRequest(target_seq=2, ts_ms=1001)
        worker._handle_kill_event(KillEvent(5, 2001, 1001, request))
        self.assertEqual(worker.publisher.records[-1]["status"], "NOT_RUNNING")
        self.assertEqual(len(worker.pending), 4)
        worker._handle_kill_event(KillEvent(6, 2002, 1001, request))
        self.assertEqual(
            [(item["kind"], item["status"], item["prev_seq"]) for item in worker.publisher.records[1:]],
            [("cmd.run.result", "KILLED", 2), ("cmd.kill.result", "REMOVED_PENDING", 6)],
        )

        with patch.object(worker, "_start_or_timeout") as start:
            self.assertTrue(worker._drain_pending())
        self.assertEqual([call.args[0] for call in start.call_args_list], [events[0], events[2], events[3]])
        self.assertFalse(worker.pending)
        self.assertFalse(worker.running)

    def test_restart_between_cancel_results_does_not_restart_target(self):
        worker = self.worker()
        worker._handle_run_event(self.run_event())
        worker.publisher.fail_kill_result = True
        with self.assertRaisesRegex(RuntimeError, "worker crashed"):
            worker._handle_kill_event(self.kill_event())

        history = [
            message(1, {"kind": "cmd.run.request", "ts_ms": 1000, "command": "printf hello", "timeout_ms": 1000}),
            message(2, {"kind": "cmd.kill.request", "ts_ms": 1001, "target_seq": 1}),
            *worker.publisher.messages,
        ]
        recovered = self.worker(history)
        recovered.runner.start = Mock(side_effect=AssertionError("cancelled command must not start"))
        with patch("openevent.cmd_worker.worker.now_ms", return_value=1500):
            recovered.startup()

        recovered.runner.start.assert_not_called()
        self.assertEqual(
            [(item["kind"], item["status"]) for item in recovered.publisher.records],
            [("cmd.kill.result", "NOT_RUNNING")],
        )
        self.assertFalse(recovered.pending)
        self.assertFalse(recovered.running)

    def test_accepted_delay_past_deadline_does_not_start_command(self):
        worker = self.worker()
        clock = [1500]
        worker.publisher.on_accepted = lambda: clock.__setitem__(0, 2100)
        worker.runner.start = Mock(side_effect=AssertionError("expired command must not start"))
        with patch("openevent.cmd_worker.worker.now_ms", side_effect=lambda: clock[0]):
            worker._handle_run_event(self.run_event())
            worker._drain_pending()

        worker.runner.start.assert_not_called()
        self.assertEqual([item["kind"] for item in worker.publisher.records], ["cmd.run.accepted", "cmd.run.result"])
        self.assertEqual(worker.publisher.records[-1]["status"], "TIMEOUT")
        self.assertNotIn("started_at_ms", worker.publisher.records[-1])
        self.assertFalse(worker.pending)
        self.assertFalse(worker.running)

    def test_second_kill_preserves_killed_status_before_completion_is_drained(self):
        worker = self.worker()
        task = self.running_task(worker)

        worker._handle_kill_event(self.kill_event(2))
        worker._handle_kill_event(self.kill_event(3))
        worker._wait_task(task)
        worker._drain_completions()

        self.assertEqual(
            [(item["kind"], item["status"]) for item in worker.publisher.records],
            [("cmd.kill.result", "KILL_SENT"), ("cmd.run.result", "KILLED"), ("cmd.kill.result", "NOT_RUNNING")],
        )
        self.assertNotIn("exit_code", worker.publisher.records[1])
        self.assertFalse(worker.running)
        self.assertFalse(worker.pending)

    def test_kill_after_timeout_preserves_timeout_status(self):
        worker = self.worker()
        task = self.running_task(worker)

        def wait(timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired("test command", timeout)
            return task.process.returncode

        task.process.wait = Mock(side_effect=wait)
        worker._wait_task(task)
        worker._handle_kill_event(self.kill_event())
        worker._drain_completions()

        self.assertEqual(
            [(item["kind"], item["status"]) for item in worker.publisher.records],
            [("cmd.run.result", "TIMEOUT"), ("cmd.kill.result", "NOT_RUNNING")],
        )
        self.assertNotIn("exit_code", worker.publisher.records[0])
        self.assertFalse(worker.pending)
        self.assertFalse(worker.running)

    def test_completion_releases_request_and_running_task(self):
        worker = self.worker()
        task = self.running_task(worker, returncode=0)
        worker._wait_task(task)
        self.assertTrue(worker._drain_completions())

        self.assertEqual(worker.publisher.records[-1]["status"], "SUCCESS")
        self.assertFalse(worker.running)
        self.assertFalse(worker.pending)

    def test_result_publication_failure_keeps_active_commands_available_for_cleanup(self):
        worker = self.worker()
        completed = self.running_task(worker, returncode=0)
        other = self.running_task(worker, seq=2)
        active = worker.running[(2001, 1)]
        worker.publisher.publish_run_result = Mock(side_effect=RuntimeError("cannot persist result"))
        worker.completions.put(CompletionEvent(2001, 1, 0))

        with self.assertRaisesRegex(RuntimeError, "cannot persist result"):
            worker._drain_completions()

        self.assertIs(worker.running[(2001, 1)], active)
        self.assertEqual(active.request.seq, 1)
        self.assertEqual(set(worker.running), {(2001, 1), (2001, 2)})
        worker.stop()

        self.assertEqual([call.args for call in worker.runner.terminate.call_args_list],
                         [(completed, "KILLED"), (other, "KILLED")])
        self.assertEqual([call.args for call in worker.runner.wait.call_args_list],
                         [(completed,), (other,)])
        self.assertEqual(completed.process.returncode, 0)
        self.assertEqual(other.process.returncode, -9)
        self.assertFalse(worker.publisher.records)

    def test_completed_and_unknown_targets_both_report_not_running_after_restart(self):
        worker = self.worker()
        task = self.running_task(worker, returncode=0)
        worker._wait_task(task)
        worker._drain_completions()
        history = [
            message(1, {"kind": "cmd.run.request", "ts_ms": 1000, "command": "true"}),
            message(2, {"kind": "cmd.run.accepted", "ts_ms": 1000, "prev_seq": 1}, 30001),
            *worker.publisher.messages,
        ]

        for restart in (False, True):
            with self.subTest(restart=restart):
                if restart:
                    worker = self.worker(history)
                    worker.runner.start = Mock(side_effect=AssertionError("finished command must not restart"))
                    worker.startup()
                    worker.runner.start.assert_not_called()
                    self.assertFalse(worker.publisher.records)
                start = len(worker.publisher.records)
                for seq, target_seq in ((101, 1), (102, 999999)):
                    worker._handle_kill_event(KillEvent(
                        seq, 2001, 1001, CmdKillRequest(target_seq=target_seq, ts_ms=1001),
                    ))
                self.assertEqual(
                    [(item["kind"], item["status"]) for item in worker.publisher.records[start:]],
                    [("cmd.kill.result", "NOT_RUNNING"), ("cmd.kill.result", "NOT_RUNNING")],
                )
                self.assertFalse(worker.running)
                self.assertFalse(worker.pending)

    def test_late_duplicate_completion_does_not_publish_again_or_affect_next_run(self):
        worker = self.worker()
        self.running_task(worker, returncode=0)
        completion = CompletionEvent(2001, 1, 0)
        worker.completions.put(completion)
        worker._drain_completions()
        next_task = self.running_task(worker, seq=2)

        worker.completions.put(completion)
        worker._drain_completions()

        self.assertEqual([(item["prev_seq"], item["status"]) for item in worker.publisher.records],
                         [(1, "SUCCESS")])
        self.assertEqual(list(worker.running), [(2001, 2)])
        self.assertIs(worker.running[(2001, 2)].command, next_task)
        self.assertEqual(worker.running[(2001, 2)].request.seq, 2)

    def test_many_completed_runs_leave_no_task_history_in_memory(self):
        worker = self.worker()
        references = []
        for seq in range(1, 257):
            task = self.running_task(worker, returncode=0, seq=seq)
            references.extend((weakref.ref(task), weakref.ref(worker.running[(2001, seq)].request)))
            worker.completions.put(CompletionEvent(2001, seq, 0))
            worker._drain_completions()
            del task
            self.assertFalse(worker.running)
            self.assertFalse(worker.pending)

        self.assertEqual(len(worker.publisher.records), 256)
        self.assertTrue(all(reference() is None for reference in references))
        self.assertFalse(hasattr(worker, "finished_runs"))
        self.assertFalse(hasattr(worker, "requests"))
        self.assertTrue(worker.completions.empty())

    def test_completed_queries_kills_and_reply_readback_retain_no_task_state(self):
        self.config = replace(self.config, channel_ids=(2001, 2002))
        worker = self.worker()
        worker.resolver.validate_startup()
        task = self.running_task(worker, returncode=0)
        output_path = self.config.output_dir / str(task.channel_id) / f"{task.seq}.stdout"
        output_path.parent.mkdir(parents=True)
        output_path.write_bytes(b"hello")
        worker._wait_task(task)
        worker._drain_completions()

        for seq in range(2, 66, 2):
            request = CmdOutputReadRequest(target_seq=1, stream="stdout", offset=0, nbytes=5, ts_ms=1001)
            worker._handle_event(OutputReadEvent(seq, 2001, 1001, request))
            self.assertEqual(worker.publisher.records[-1]["content"], "hello")
            worker._handle_event(self.kill_event(seq + 1))
            self.assertEqual(worker.publisher.records[-1]["status"], "NOT_RUNNING")

        # Replies are read back on later Fetch rounds, after local publication
        # has already released the completed run.
        worker._observe_realtime_message(
            message(99, {"kind": "cmd.run.accepted", "prev_seq": 1, "ts_ms": 1000}, 30001)
        )
        for item in worker.publisher.messages:
            worker._observe_realtime_message(item)
        worker._handle_event(KillEvent(66, 2002, 1001, CmdKillRequest(target_seq=1, ts_ms=1001)))

        self.assertEqual(worker.publisher.records[-1]["status"], "NOT_RUNNING")
        self.assertFalse(hasattr(worker, "finished_runs"))
        self.assertFalse(worker.running)
        self.assertFalse(worker.pending)
        self.assertFalse(hasattr(worker, "accepted"))
        self.assertFalse(hasattr(worker, "results"))

    def test_full_slots_with_pending_command_sleep_when_no_progress(self):
        worker = self.worker()
        self.running_task(worker)
        worker._handle_run_event(self.run_event(seq=2))
        worker.startup = Mock()
        worker.stop = Mock()
        worker._fetch_round = Mock(side_effect=[(False, False), AssertionError("worker polled again without sleeping")])

        def stop_after_sleep(seconds):
            worker.stopping = True

        with patch("openevent.cmd_worker.worker.time.sleep", side_effect=stop_after_sleep) as sleep:
            worker.run()

        sleep.assert_called_once_with(self.config.fetch_idle_sleep_ms / 1000)
        self.assertEqual(len(worker.pending), 1)
        worker.stop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
