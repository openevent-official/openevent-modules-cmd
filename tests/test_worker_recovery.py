from __future__ import annotations

import tempfile
import unittest
import weakref
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openevent.cmd_sdk.model import KIND_KILL_RESULT, KIND_OUTPUT_READ_RESULT, KIND_RUN_RESULT
from openevent.cmd_worker.config import CmdWorkerConfig, OpenEventConfig
from openevent.cmd_worker.worker import CmdWorker, CompletionEvent

from test_worker_lifecycle import FakeClient, RecordingPublisher, message


class WorkerRecoveryTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config = CmdWorkerConfig(
            open_event=OpenEventConfig("unused"),
            principal=30001, token="token", channel_ids=(2001,),
            output_dir=Path(directory.name), default_timeout_ms=600000,
        )

    def test_scan_fixes_first_boundary_and_retains_only_unresolved_requests(self):
        requests = [message(seq, {"kind": "cmd.run.request", "ts_ms": 1000, "command": "true"})
                    for seq in (1, 2, 6)]
        accepted = message(3, {"kind": "cmd.run.accepted", "prev_seq": 1, "ts_ms": 1000}, 30001)
        result = message(4, {
            "kind": KIND_RUN_RESULT, "prev_seq": 1, "ts_ms": 1000,
            "status": "SUCCESS", "timeout_ms": 300000, "exit_code": 0,
            "stdout": {"file": "2001/1.stdout", "bytes": 0, "content_encoding": "utf-8", "content": ""},
            "stderr": {"file": "2001/1.stderr", "bytes": 0, "content_encoding": "utf-8", "content": ""},
        }, 30001)
        kill = message(5, {"kind": "cmd.kill.request", "ts_ms": 1000, "target_seq": 2})
        client = FakeClient()
        client.fetch = Mock(side_effect=[
            SimpleNamespace(messages=requests[:2], next_seq=3, last_seq=5),
            # last_seq is deliberately absent: later pages must not read it.
            SimpleNamespace(messages=[accepted, result, kill, requests[2]], next_seq=7),
            SimpleNamespace(messages=[requests[2]], next_seq=7, last_seq=6),
        ])
        worker = CmdWorker(self.config, client)
        worker.resolver.validate_startup()
        recovery = worker._scan_history()
        self.assertEqual([event.seq for event in recovery.requests.values()], [2, 5])
        self.assertNotIn((2001, 1), recovery.requests)
        self.assertFalse(recovery.accepted)
        self.assertEqual(worker.next_fetch_seq, 6)
        self.assertEqual(worker._fetch_round(), (True, False))
        self.assertEqual([event.seq for event in worker.pending.values()], [6])
        self.assertEqual([call.kwargs["from_seq"] for call in client.fetch.call_args_list], [1, 3, 6])

    def test_empty_filtered_page_does_not_end_recovery_early(self):
        request = message(5, {"kind": "cmd.run.request", "ts_ms": 1000, "command": "true"})
        client = FakeClient()
        client.fetch = Mock(side_effect=[
            SimpleNamespace(messages=[], next_seq=5, last_seq=5),
            SimpleNamespace(messages=[request], next_seq=6),
        ])
        worker = CmdWorker(self.config, client)
        worker.resolver.validate_startup()
        completion = CompletionEvent(2001, 99, 0)
        worker.completions.put(completion)
        self.assertEqual([event.seq for event in worker._scan_history().requests.values()], [5])
        self.assertEqual(worker.next_fetch_seq, 6)
        self.assertIs(worker.completions.get_nowait(), completion)

    def test_nonadvancing_page_fails_on_first_or_later_recovery_page(self):
        for stalled_seq in (1, 2):
            with self.subTest(stalled_seq=stalled_seq):
                client = FakeClient()
                worker = CmdWorker(self.config, client)
                pages = []
                if stalled_seq == 2:
                    pages.append(SimpleNamespace(messages=[], next_seq=2, last_seq=2))
                pages.append(SimpleNamespace(messages=[], next_seq=stalled_seq, last_seq=2))
                client.fetch = Mock(side_effect=pages)

                with self.assertRaisesRegex(RuntimeError, "Fetch did not advance next_seq"):
                    worker._scan_history()

    def test_scan_closes_all_request_kinds_across_pages_and_channels(self):
        def item(seq, channel_id, payload, principal=1001):
            result = message(seq, {"ts_ms": 1000, **payload}, principal)
            result.channel_id = channel_id
            return result

        run = {"kind": "cmd.run.request", "command": "true"}
        kill = {"kind": "cmd.kill.request", "target_seq": 99}
        output = {"kind": "cmd.output.read.request", "target_seq": 99,
                  "stream": "stdout", "offset": 0, "nbytes": 10}
        history = [
            item(1, 2001, run),
            item(2, 2002, kill),
            item(3, 2001, output),
            item(4, 2002, run),
            item(5, 2002, run),
            item(6, 2001, kill),
            item(7, 2002, output),
            item(8, 2001, output),
            item(9, 2003, run),
            item(10, 2001, _run_result(1, 2001, "START_FAILED"), 30001),
            item(11, 2002, {"kind": KIND_KILL_RESULT, "prev_seq": 2,
                           "target_seq": 99, "status": "NOT_RUNNING"}, 30001),
            item(12, 2001, {"kind": KIND_OUTPUT_READ_RESULT, "prev_seq": 3,
                           "target_seq": 99, "stream": "stdout", "offset": 0, "nbytes": 10,
                           "status": "NOT_FOUND", "content": "", "bytes": 0,
                           "eof": True, "content_encoding": "utf-8"}, 30001),
            item(13, 2002, _run_result(4, 2002, "START_FAILED"), 30001),
        ]
        client = FakeClient()
        client.fetch = Mock(side_effect=[
            SimpleNamespace(messages=history[:4], next_seq=5, last_seq=13),
            SimpleNamespace(messages=history[4:10], next_seq=11),
            SimpleNamespace(messages=history[10:], next_seq=14),
        ])
        worker = CmdWorker(replace(self.config, channel_ids=(2001, 2002)), client)
        worker.resolver.validate_startup()

        recovery = worker._scan_history()
        events = list(recovery.requests.values())

        self.assertEqual([(event.channel_id, event.seq) for event in events],
                         [(2002, 5), (2001, 6), (2002, 7), (2001, 8)])
        self.assertFalse(worker.pending)
        self.assertFalse(worker.running)
        self.assertFalse(recovery.accepted)
        self.assertEqual(worker.next_fetch_seq, 14)

    def test_scan_releases_closed_requests_and_acceptances_before_next_page(self):
        client = FakeClient()
        worker = CmdWorker(self.config, client)
        worker.resolver.validate_startup()
        states = []
        event_refs = []
        observe = worker._observe_message

        def track(item, *, recovery=None):
            states[:] = [recovery]
            event = observe(item, recovery=recovery)
            if event is not None and event.seq <= 4:
                event_refs.append(weakref.ref(event))
            return event

        def fetch(principal, token, *, from_seq, **kwargs):
            if from_seq == 1:
                return SimpleNamespace(messages=[
                    message(1, {"kind": "cmd.run.request", "ts_ms": 1000, "command": "true"}),
                    message(2, {"kind": "cmd.run.accepted", "prev_seq": 1, "ts_ms": 1000}, 30001),
                    message(3, {"kind": "cmd.kill.request", "ts_ms": 1000, "target_seq": 1}),
                    message(4, {"kind": "cmd.output.read.request", "ts_ms": 1000,
                                "target_seq": 1, "stream": "stdout", "offset": 0, "nbytes": 10}),
                ], next_seq=5, last_seq=10)
            if from_seq == 5:
                self.assertEqual(len(states[0].requests), 3)
                self.assertEqual(states[0].accepted, {(2001, 1)})
                return SimpleNamespace(messages=[
                    message(5, {"ts_ms": 1000, **_run_result(1, 2001, "START_FAILED")}, 30001),
                    message(6, {"kind": KIND_KILL_RESULT, "ts_ms": 1000, "prev_seq": 3,
                                "target_seq": 1, "status": "NOT_RUNNING"}, 30001),
                    message(7, {"kind": KIND_OUTPUT_READ_RESULT, "ts_ms": 1000, "prev_seq": 4,
                                "target_seq": 1, "stream": "stdout", "offset": 0, "nbytes": 10,
                                "status": "NOT_FOUND", "content": "", "bytes": 0,
                                "eof": True, "content_encoding": "utf-8"}, 30001),
                ], next_seq=8)
            self.assertEqual(from_seq, 8)
            self.assertFalse(states[0].requests)
            self.assertFalse(states[0].accepted)
            self.assertEqual(len(event_refs), 3)
            self.assertTrue(all(reference() is None for reference in event_refs))
            return SimpleNamespace(messages=[
                message(8, {"kind": "cmd.run.request", "ts_ms": 1000, "command": "true"}),
                message(9, {"kind": "cmd.run.request", "ts_ms": 1000, "command": "true"}),
                message(10, {"kind": "cmd.run.accepted", "prev_seq": 9, "ts_ms": 1000}, 30001),
            ], next_seq=11)

        worker._observe_message = track
        client.fetch = fetch
        recovery = worker._scan_history()

        self.assertEqual([event.seq for event in recovery.requests.values()], [8, 9])
        self.assertEqual(recovery.accepted, {(2001, 9)})
        self.assertFalse(worker.pending)
        self.assertFalse(worker.running)

    def test_startup_replays_in_order_and_releases_recovery_state_before_starting(self):
        client = FakeClient([
            message(1, {"kind": "cmd.run.request", "ts_ms": 1000, "command": "true"}),
            message(2, {"kind": "cmd.run.accepted", "prev_seq": 1, "ts_ms": 1000}, 30001),
            message(3, {"kind": "cmd.run.request", "ts_ms": 1000, "command": "true"}),
            message(4, {"kind": "cmd.kill.request", "ts_ms": 1000, "target_seq": 3}),
            message(5, {"kind": "cmd.run.request", "ts_ms": 1000, "command": "true"}),
        ])
        worker = CmdWorker(self.config, client)
        worker.publisher = RecordingPublisher()
        scan = worker._scan_history
        state_refs = []

        def track_scan():
            recovery = scan()
            state_refs.append(weakref.ref(recovery))
            return recovery

        def start(event):
            self.assertEqual(event.seq, 5)
            self.assertIsNone(state_refs[0]())
            self.assertEqual([(item["kind"], item["status"]) for item in worker.publisher.records], [
                (KIND_RUN_RESULT, "RECOVERY_FAILED"),
                (KIND_RUN_RESULT, "KILLED"),
                (KIND_KILL_RESULT, "REMOVED_PENDING"),
            ])

        with patch.object(worker, "_scan_history", side_effect=track_scan), \
                patch.object(worker, "_start_or_timeout", side_effect=start) as start_command:
            worker.startup()

        start_command.assert_called_once()
        self.assertFalse(worker.pending)
        self.assertFalse(worker.running)

    def test_recovery_uses_current_default_only_when_request_omits_timeout(self):
        for requested, expected in ((None, 600000), (10000, 10000)):
            with self.subTest(requested=requested):
                payload = {"kind": "cmd.run.request", "ts_ms": 1000, "command": "true"}
                if requested is not None:
                    payload["timeout_ms"] = requested
                client = FakeClient([
                    message(1, payload),
                    message(2, {"kind": "cmd.run.accepted", "prev_seq": 1, "ts_ms": 1001}, 30001),
                ])
                worker = CmdWorker(self.config, client)
                worker.publisher = RecordingPublisher()
                worker.runner.start = Mock(side_effect=AssertionError("accepted task must not restart"))
                worker.startup()
                worker.runner.start.assert_not_called()
                self.assertEqual(len(worker.publisher.records), 1)
                result = worker.publisher.records[0]
                self.assertEqual(result["status"], "RECOVERY_FAILED")
                self.assertEqual(result["timeout_ms"], expected)


def _run_result(prev_seq, channel_id, status):
    payload = {
        "kind": KIND_RUN_RESULT, "prev_seq": prev_seq, "status": status,
        "error_message": "test failure", "timeout_ms": 300000,
        "stdout": {"file": f"{channel_id}/{prev_seq}.stdout", "bytes": 0,
                   "content_encoding": "utf-8", "content": ""},
        "stderr": {"file": f"{channel_id}/{prev_seq}.stderr", "bytes": 0,
                   "content_encoding": "utf-8", "content": ""},
    }
    return payload


if __name__ == "__main__":
    unittest.main()
