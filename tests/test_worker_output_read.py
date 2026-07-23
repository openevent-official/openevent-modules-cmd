from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from openevent.cmd_sdk.model import KIND_OUTPUT_READ_REQUEST, CmdOutputReadRequest
from openevent.cmd_worker.config import CmdWorkerConfig, OpenEventConfig
from openevent.cmd_worker.worker import CmdWorker, InvalidRequestEvent, OutputReadEvent


class FakeClient:
    pass


class FakePublisher:
    def __init__(self) -> None:
        self.output_read_result = None

    def publish_output_read_result(self, channel_id, request_principal, result, check_from_seq):
        del channel_id, request_principal, check_from_seq
        self.output_read_result = result
        return SimpleNamespace(seq=999)


class WorkerOutputReadTest(unittest.TestCase):
    def test_run_stops_worker_after_runtime_error(self) -> None:
        worker = CmdWorker(
            CmdWorkerConfig(
                protocol="cmd.v1",
                open_event=OpenEventConfig(addr="127.0.0.1:9527"),
                principal=30001,
                token="token",
                channel_ids=(2001,),
                output_dir=Path("cmd-worker-output"),
            ),
            FakeClient(),
        )
        stopped = []
        worker.startup = lambda: None
        worker._drain_completions = lambda: False
        worker._fetch_round = lambda: (_ for _ in ()).throw(RuntimeError("fetch failed"))
        worker.stop = lambda: stopped.append(True)

        with self.assertRaisesRegex(RuntimeError, "fetch failed"):
            worker.run()
        self.assertEqual(stopped, [True])

    def test_wait_task_reports_internal_error_to_main_thread(self) -> None:
        class BrokenProcess:
            def wait(self, timeout=None):
                del timeout
                raise RuntimeError("wait failed")

        worker = CmdWorker(
            CmdWorkerConfig(
                protocol="cmd.v1",
                open_event=OpenEventConfig(addr="127.0.0.1:9527"),
                principal=30001,
                token="token",
                channel_ids=(2001,),
                output_dir=Path("cmd-worker-output"),
            ),
            FakeClient(),
        )
        task = SimpleNamespace(deadline_ms=10**15, process=BrokenProcess(), channel_id=2001, seq=1)
        worker._wait_task(task)
        with self.assertRaisesRegex(RuntimeError, "failed to wait for command") as raised:
            worker._drain_completions()
        self.assertIsInstance(raised.exception.__cause__, RuntimeError)

    def test_output_read_reduces_bytes_to_fit_payload_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            channel_dir = Path(tmp) / "2001"
            channel_dir.mkdir()
            (channel_dir / "123.stdout").write_bytes(b"x" * 4096)
            config = CmdWorkerConfig(
                protocol="cmd.v1",
                open_event=OpenEventConfig(addr="127.0.0.1:9527", max_payload_bytes=512),
                principal=30001,
                token="token",
                channel_ids=(2001,),
                output_dir=Path(tmp),
            )
            worker = CmdWorker(config, FakeClient())
            event = OutputReadEvent(
                seq=124,
                channel_id=2001,
                principal=1001,
                request=CmdOutputReadRequest(target_seq=123, stream="stdout", offset=0, nbytes=4096, ts_ms=1),
            )
            result = worker._build_output_read_result(event)
            self.assertEqual(result.status, "SUCCESS")
            self.assertLess(result.bytes, 4096)
            self.assertFalse(result.eof)

    def test_invalid_output_read_does_not_echo_bool_int_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = CmdWorkerConfig(
                protocol="cmd.v1",
                open_event=OpenEventConfig(addr="127.0.0.1:9527"),
                principal=30001,
                token="token",
                channel_ids=(2001,),
                output_dir=Path(tmp),
            )
            worker = CmdWorker(config, FakeClient())
            publisher = FakePublisher()
            worker.publisher = publisher
            worker._handle_invalid_request_event(
                InvalidRequestEvent(
                    seq=124,
                    channel_id=2001,
                    principal=1001,
                    kind=KIND_OUTPUT_READ_REQUEST,
                    error_message="bad request",
                    payload=b'{"kind":"cmd.output.read.request","ts_ms":1,"target_seq":true,"stream":"stdout","offset":false,"nbytes":true}',
                )
            )
            self.assertIsNone(publisher.output_read_result.target_seq)
            self.assertIsNone(publisher.output_read_result.offset)
            self.assertIsNone(publisher.output_read_result.nbytes)


if __name__ == "__main__":
    unittest.main()
