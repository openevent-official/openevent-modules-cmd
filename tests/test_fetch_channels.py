from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import grpc

from openevent.cmd_worker.config import CmdWorkerConfig, OpenEventConfig
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

    def test_rpc_failure_is_retried(self) -> None:
        response = SimpleNamespace(messages=[], next_seq=1, last_seq=0)
        client = SimpleNamespace(fetch=Mock(side_effect=[grpc.RpcError(), response]))
        worker = CmdWorker(_config(), client)
        with patch("openevent.cmd_worker.worker.time.sleep") as sleep:
            self.assertIs(worker._fetch_with_retries(1), response)
        self.assertEqual(client.fetch.call_count, 2)
        sleep.assert_called_once_with(worker.config.fetch_error_retry_sleep_ms / 1000)

    def test_local_error_is_not_retried(self) -> None:
        failure = RuntimeError("local implementation failure")
        client = SimpleNamespace(fetch=Mock(side_effect=failure))
        worker = CmdWorker(_config(), client)
        with patch("openevent.cmd_worker.worker.time.sleep") as sleep:
            with self.assertRaises(RuntimeError) as raised:
                worker._fetch_with_retries(1)
        self.assertIs(raised.exception, failure)
        client.fetch.assert_called_once()
        sleep.assert_not_called()

    def test_exhausted_rpc_retries_clean_running_tasks_once_on_exit(self) -> None:
        failure = grpc.RpcError()
        client = SimpleNamespace(fetch=Mock(side_effect=failure))
        worker = CmdWorker(_config(), client)
        worker.startup = Mock()
        task = SimpleNamespace(channel_id=2001, seq=1)
        worker.running[(2001, 1)] = task
        worker.runner.terminate = Mock()
        worker.runner.wait = Mock()

        with patch("openevent.cmd_worker.worker.time.sleep") as sleep:
            with self.assertRaises(grpc.RpcError) as raised:
                worker.run()

        self.assertIs(raised.exception, failure)
        self.assertEqual(client.fetch.call_count, worker.config.fetch_error_max_retries + 1)
        self.assertEqual(sleep.call_count, worker.config.fetch_error_max_retries)
        self.assertTrue(worker.stopping)
        worker.runner.terminate.assert_called_once_with(task, "KILLED")
        worker.runner.wait.assert_called_once_with(task)

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
