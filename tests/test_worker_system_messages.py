from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openevent.cmd_sdk import parse_message
from openevent.cmd_worker.config import CmdWorkerConfig, OpenEventConfig
from openevent.cmd_worker.worker import CmdWorker

from test_worker_lifecycle import FakeClient, RecordingPublisher, message


def system_message(seq, kind, **data):
    return SimpleNamespace(
        seq=seq, channel_id=2001, principal=0, recipients=[], object_keys=[],
        ts_ms=1000, uuid=seq,
        payload=json.dumps({"kind": kind, "data": {"schema_version": 1, **data}}).encode("utf-8"),
    )


def initialized_message(seq):
    return system_message(
        seq, "system.channel_initialized", name="commands", protocol="cmd.v3",
        description="", visibility="private", creator=1001, members=[1001, 30001],
    )


def run_message(seq):
    return message(seq, {"kind": "cmd.run.request", "ts_ms": 1000, "command": "true"})


class WorkerSystemMessagesTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        config = CmdWorkerConfig(
            open_event=OpenEventConfig("unused"),
            principal=30001, token="token", channel_ids=(2001,),
            output_dir=Path(directory.name),
        )
        self.client = FakeClient()
        self.worker = CmdWorker(config, self.client)
        self.worker.publisher = RecordingPublisher()

    def test_startup_ignores_system_messages_and_recovers_command_state_across_pages(self):
        self.client.fetch = Mock(side_effect=[
            SimpleNamespace(messages=[initialized_message(1), run_message(2)], next_seq=3, last_seq=6),
            SimpleNamespace(messages=[
                system_message(3, "system.member_added", member=1002),
                message(4, {"kind": "cmd.run.accepted", "prev_seq": 2, "ts_ms": 1000}, 30001),
            ], next_seq=5),
            SimpleNamespace(messages=[
                system_message(5, "system.member_removed", member=1002), run_message(6),
            ], next_seq=7),
        ])

        with (
            self.assertNoLogs("openevent.cmd_worker.worker", level="WARNING"),
            patch("openevent.cmd_worker.worker.parse_message", wraps=parse_message) as parse,
            patch.object(self.worker, "_publish_recovery_failed") as recovered,
            patch.object(self.worker, "_start_or_timeout") as start,
        ):
            self.worker.startup()

        self.assertEqual([call.args[0].seq for call in parse.call_args_list], [2, 4, 6])
        self.assertEqual([call.args[0].seq for call in recovered.call_args_list], [2])
        self.assertEqual([call.args[0].seq for call in start.call_args_list], [6])
        self.assertEqual([call.kwargs["from_seq"] for call in self.client.fetch.call_args_list], [1, 3, 5])
        self.assertEqual(self.worker.next_fetch_seq, 7)
        self.assertFalse(self.worker.pending)
        self.assertFalse(self.worker.running)
        self.assertFalse(self.worker.publisher.records)

    def test_realtime_system_only_page_advances_and_later_commands_remain_ordered(self):
        self.worker.resolver.validate_startup()
        self.client.fetch = Mock(side_effect=[
            SimpleNamespace(messages=[initialized_message(1)], next_seq=2, last_seq=5),
            SimpleNamespace(messages=[
                run_message(2), system_message(3, "system.member_added", member=1002),
            ], next_seq=4),
            SimpleNamespace(messages=[
                system_message(4, "system.member_removed", member=1002), run_message(5),
            ], next_seq=6),
        ])

        with (
            self.assertNoLogs("openevent.cmd_worker.worker", level="WARNING"),
            patch("openevent.cmd_worker.worker.parse_message", wraps=parse_message) as parse,
        ):
            self.assertEqual(self.worker._fetch_round(), (True, False))

        self.assertEqual([call.args[0].seq for call in parse.call_args_list], [2, 5])
        self.assertEqual(list(self.worker.pending), [(2001, 2), (2001, 5)])
        self.assertEqual([call.kwargs["from_seq"] for call in self.client.fetch.call_args_list], [1, 2, 4])
        self.assertEqual(self.worker.next_fetch_seq, 6)
        self.assertFalse(self.worker.running)
        self.assertFalse(self.worker.publisher.records)
        with patch.object(self.worker, "_start_or_timeout") as start:
            self.assertTrue(self.worker._drain_pending())
        self.assertEqual([call.args[0].seq for call in start.call_args_list], [2, 5])


if __name__ == "__main__":
    unittest.main()
