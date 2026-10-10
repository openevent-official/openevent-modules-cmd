from __future__ import annotations

import tempfile
import unittest
import weakref
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from openevent.cmd_sdk.model import CmdRunRequest
from openevent.cmd_worker.config import CmdWorkerConfig, OpenEventConfig
from openevent.cmd_worker.runner import RunningCommand
from openevent.cmd_worker.worker import ActiveRun, CmdWorker, CompletionEvent, RunEvent

from test_worker_lifecycle import FakeClient, FakeProcess, RecordingPublisher, message


class TrackedNamespace(SimpleNamespace):
    pass


class WorkerPagingTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config = CmdWorkerConfig(
            open_event=OpenEventConfig("unused"),
            principal=30001, token="token", channel_ids=(2001,),
            output_dir=Path(directory.name), max_concurrent_tasks=1,
        )
        self.client = FakeClient()
        self.worker = CmdWorker(self.config, self.client)
        self.worker.resolver.validate_startup()
        self.worker.publisher = RecordingPublisher()

    def test_first_page_kill_is_processed_before_fetching_next_page(self):
        self.worker._handle_run_event(_run_event(1))
        calls = []

        def fetch(principal, token, *, from_seq, **kwargs):
            calls.append(from_seq)
            if from_seq == 1:
                return SimpleNamespace(messages=[_kill_message(2, 1)], next_seq=3, last_seq=3)
            self.assertEqual(from_seq, 3)
            self.assertFalse(self.worker.pending)
            self.assertEqual([item["status"] for item in self.worker.publisher.records],
                             ["KILLED", "REMOVED_PENDING"])
            return SimpleNamespace(messages=[], next_seq=4, last_seq=100)

        self.client.fetch = fetch

        self.assertEqual(self.worker._fetch_round(), (True, False))
        self.assertEqual(calls, [1, 3])
        self.assertEqual(self.worker.next_fetch_seq, 4)

    def test_completion_is_published_and_released_between_filtered_pages(self):
        event = _run_event(1)
        task = RunningCommand(
            channel_id=2001, seq=1, process=FakeProcess(returncode=0),
            started_at_ms=1000, timeout_ms=1000, deadline_ms=2000,
        )
        self.worker.running[(2001, 1)] = ActiveRun(event, task)

        def fetch(principal, token, *, from_seq, **kwargs):
            if from_seq == 1:
                self.worker.completions.put(CompletionEvent(2001, 1, 0))
                return SimpleNamespace(messages=[], next_seq=3, last_seq=3)
            self.assertEqual(from_seq, 3)
            self.assertFalse(self.worker.running)
            self.assertFalse(self.worker.pending)
            self.assertEqual(self.worker.publisher.records[0]["status"], "SUCCESS")
            return SimpleNamespace(messages=[], next_seq=4)

        self.client.fetch = fetch

        self.assertEqual(self.worker._fetch_round(), (False, True))

    def test_completion_is_published_after_the_final_page(self):
        self.worker.running[(2001, 1)] = ActiveRun(_run_event(1), RunningCommand(
            channel_id=2001, seq=1, process=FakeProcess(returncode=0),
            started_at_ms=1000, timeout_ms=1000, deadline_ms=2000,
        ))

        def fetch(principal, token, *, from_seq, **kwargs):
            self.worker.completions.put(CompletionEvent(2001, 1, 0))
            return SimpleNamespace(messages=[], next_seq=2, last_seq=1)

        self.client.fetch = fetch

        self.assertEqual(self.worker._fetch_round(), (False, True))
        self.assertFalse(self.worker.running)
        self.assertFalse(self.worker.pending)
        self.assertEqual(self.worker.publisher.records[0]["status"], "SUCCESS")

    def test_new_runs_start_only_after_fixed_boundary_and_same_round_kill(self):
        self.worker.startup = Mock()
        self.worker.stop = Mock()
        self.worker._start_or_timeout = Mock()
        calls = []

        def fetch(principal, token, *, from_seq, **kwargs):
            calls.append(from_seq)
            self.worker._start_or_timeout.assert_not_called()
            if from_seq == 1:
                return SimpleNamespace(messages=[_run_message(1)], next_seq=2, last_seq=3)
            self.assertEqual(from_seq, 2)
            self.assertEqual(list(self.worker.pending), [(2001, 1)])
            return SimpleNamespace(
                messages=[_kill_message(2, 1), _run_message(3), _run_message(4)],
                next_seq=5, last_seq=99,
            )

        def start(event):
            self.assertEqual(event.seq, 3)
            self.assertEqual(self.worker.next_fetch_seq, 4)
            self.assertEqual(calls, [1, 2])
            self.assertEqual([item["status"] for item in self.worker.publisher.records],
                             ["KILLED", "REMOVED_PENDING"])
            self.worker.stopping = True

        self.client.fetch = fetch
        self.worker._start_or_timeout.side_effect = start

        self.worker.run()

        self.worker._start_or_timeout.assert_called_once()
        self.assertNotIn((2001, 4), self.worker.pending)
        self.assertNotIn((2001, 4), self.worker.running)
        self.worker.stop.assert_called_once_with()

    def test_empty_filtered_first_page_does_not_end_round(self):
        self.client.fetch = Mock(side_effect=[
            SimpleNamespace(messages=[], next_seq=5, last_seq=5),
            SimpleNamespace(messages=[_run_message(5)], next_seq=6),
        ])

        self.assertEqual(self.worker._fetch_round(), (True, False))

        self.assertEqual(list(self.worker.pending), [(2001, 5)])
        self.assertEqual(self.worker.next_fetch_seq, 6)
        self.assertEqual([call.kwargs["from_seq"] for call in self.client.fetch.call_args_list], [1, 5])

    def test_previous_page_and_last_message_are_released_before_next_fetch(self):
        for mode in ("history", "realtime"):
            with self.subTest(mode=mode):
                references = []
                self.worker.next_fetch_seq = 1
                self.worker._observe_message = lambda item, **kwargs: None

                def fetch(principal, token, *, from_seq, **kwargs):
                    if from_seq == 1:
                        item = TrackedNamespace(seq=1, payload=b"x" * 1024)
                        page = TrackedNamespace(messages=[item], next_seq=2, last_seq=2)
                        references.extend([weakref.ref(page), weakref.ref(item)])
                        return page
                    self.assertEqual(from_seq, 2)
                    self.assertTrue(all(reference() is None for reference in references))
                    return SimpleNamespace(messages=[], next_seq=3)

                self.client.fetch = fetch

                if mode == "history":
                    self.assertFalse(self.worker._scan_history().requests)
                else:
                    self.assertEqual(self.worker._fetch_round(), (True, False))

    def test_nonadvancing_page_fails_on_first_or_later_page(self):
        for stalled_seq in (1, 2):
            with self.subTest(stalled_seq=stalled_seq):
                self.worker.next_fetch_seq = 1
                pages = []
                if stalled_seq == 2:
                    pages.append(SimpleNamespace(messages=[], next_seq=2, last_seq=2))
                pages.append(SimpleNamespace(messages=[], next_seq=stalled_seq, last_seq=2))
                self.client.fetch = Mock(side_effect=pages)
                with self.assertRaisesRegex(RuntimeError, "Fetch did not advance next_seq"):
                    self.worker._fetch_round()


def _run_event(seq):
    return RunEvent(seq, 2001, 1001, 1000,
                    CmdRunRequest(command="true", ts_ms=1000, timeout_ms=1000))


def _run_message(seq):
    return message(seq, {"kind": "cmd.run.request", "ts_ms": 1000, "command": "true"})


def _kill_message(seq, target_seq):
    return message(seq, {"kind": "cmd.kill.request", "ts_ms": 1000, "target_seq": target_seq})


if __name__ == "__main__":
    unittest.main()
