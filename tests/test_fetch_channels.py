from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from openevent.cmd_worker.config import CmdWorkerConfig, OpenEventConfig
from openevent.cmd_worker.publisher import PublishFatal, ReliablePublisher
from openevent.cmd_worker.worker import CmdWorker


class FetchWithChannelsClient:
    def __init__(self) -> None:
        self.calls = []

    def fetch(self, principal: int, token: str, from_seq: int, limit: int, only_my_recipient: bool = False, channels=()):
        self.calls.append(
            {
                "principal": principal,
                "token": token,
                "from_seq": from_seq,
                "limit": limit,
                "only_my_recipient": only_my_recipient,
                "channels": tuple(channels),
            }
        )
        return SimpleNamespace(messages=[], next_seq=1, last_seq=0)


class FetchChannelsTest(unittest.TestCase):
    def test_worker_fetch_uses_configured_channels(self) -> None:
        client = FetchWithChannelsClient()
        worker = CmdWorker(_config(), client)
        worker._fetch_with_retries(1)
        self.assertEqual(client.calls[0]["channels"], (2001, 2002))
        self.assertFalse(client.calls[0]["only_my_recipient"])

    def test_reliable_publisher_fetch_uses_configured_channels(self) -> None:
        client = FetchWithChannelsClient()
        publisher = ReliablePublisher(
            protocol_client=SimpleNamespace(),
            openevent_client=client,
            principal=30001,
            token="token",
            channel_ids=(2001,),
            fetch_error_max_retries=1,
            fetch_error_retry_sleep_ms=1,
        )
        publisher._fetch_with_retries(1)
        self.assertEqual(client.calls[0]["channels"], (2001,))

    def test_publish_reconciliation_detects_duplicates_across_pages(self) -> None:
        messages = [
            SimpleNamespace(
                seq=seq,
                channel_id=2001,
                principal=30001,
                recipients=(),
                payload=(
                    b'{"kind":"cmd.run.accepted","ts_ms":1,"prev_seq":10}'
                    if seq in {2, 4}
                    else b'{"kind":"cmd.kill.request","ts_ms":1,"target_seq":10}'
                ),
            )
            for seq in range(1, 5)
        ]

        class PagedClient(FetchWithChannelsClient):
            def fetch(self, principal, token, from_seq, limit, only_my_recipient=False, channels=()):
                del principal, token, limit, only_my_recipient, channels
                page = [message for message in messages if from_seq <= message.seq < from_seq + 2]
                return SimpleNamespace(messages=page, next_seq=from_seq + 2, last_seq=4)

        publisher = ReliablePublisher(SimpleNamespace(), PagedClient(), 30001, "token", (2001,), 1, 1)
        with self.assertRaises(PublishFatal):
            publisher._find_existing(2001, "cmd.run.accepted", 10, 1)


def _config() -> CmdWorkerConfig:
    return CmdWorkerConfig(
        protocol="cmd.v1",
        open_event=OpenEventConfig(addr="127.0.0.1:9527"),
        principal=30001,
        token="token",
        channel_ids=(2001, 2002),
        output_dir=Path("cmd-worker-output"),
    )


if __name__ == "__main__":
    unittest.main()
