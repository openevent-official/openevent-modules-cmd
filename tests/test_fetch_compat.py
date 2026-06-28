from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from openevent.cmd_worker.config import CmdWorkerConfig, OpenEventConfig
from openevent.cmd_worker.publisher import ReliablePublisher
from openevent.cmd_worker.worker import CmdWorker


class FetchWithoutChannelsClient:
    def __init__(self) -> None:
        self.calls = []

    def fetch(self, principal: int, token: str, from_seq: int, limit: int, only_my_recipient: bool = False):
        self.calls.append(
            {
                "principal": principal,
                "token": token,
                "from_seq": from_seq,
                "limit": limit,
                "only_my_recipient": only_my_recipient,
            }
        )
        return SimpleNamespace(messages=[], next_seq=1, last_seq=0)


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


class FetchCompatTest(unittest.TestCase):
    def test_worker_fetch_supports_installed_sdk_without_channels_argument(self) -> None:
        client = FetchWithoutChannelsClient()
        worker = CmdWorker(_config(), client)
        worker._fetch_with_retries(1)
        self.assertNotIn("channels", client.calls[0])
        self.assertFalse(client.calls[0]["only_my_recipient"])

    def test_worker_fetch_uses_channels_when_sdk_supports_it(self) -> None:
        client = FetchWithChannelsClient()
        worker = CmdWorker(_config(), client)
        worker._fetch_with_retries(1)
        self.assertEqual(client.calls[0]["channels"], (2001, 2002))

    def test_reliable_publisher_fetch_supports_installed_sdk_without_channels_argument(self) -> None:
        client = FetchWithoutChannelsClient()
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
        self.assertNotIn("channels", client.calls[0])


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

