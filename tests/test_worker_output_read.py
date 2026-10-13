from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from openevent.cmd_sdk.codec import dict_to_model, dumps_payload, loads_payload, output_read_result_input_to_dict
from openevent.cmd_sdk.model import CmdOutputReadRequest
from openevent.cmd_worker.config import CmdWorkerConfig, OpenEventConfig
from openevent.cmd_worker.worker import CmdWorker, OutputReadEvent


class FakeClient:
    def __init__(self):
        self.payloads = []

    def get_uuid(self):
        return len(self.payloads) + 1

    def publish_auto_seq(self, *, payload, **kwargs):
        self.payloads.append(payload)
        return SimpleNamespace(seq=len(self.payloads))


class WorkerOutputReadTest(unittest.TestCase):
    def make_worker(self, output_dir: Path, max_payload_bytes: int = 1024) -> CmdWorker:
        return CmdWorker(
            CmdWorkerConfig(
                open_event=OpenEventConfig(addr="127.0.0.1:9527", max_payload_bytes=max_payload_bytes),
                principal=30001,
                token="token",
                channel_ids=(2001,),
                output_dir=output_dir,
            ),
            FakeClient(),
        )

    def read_event(self, offset: int, nbytes: int, seq: int = 124) -> OutputReadEvent:
        return OutputReadEvent(
            seq=seq,
            channel_id=2001,
            principal=1001,
            request=CmdOutputReadRequest(target_seq=123, stream="stdout", offset=offset, nbytes=nbytes, ts_ms=1),
        )

    def write_output(self, output_dir: Path, data: bytes) -> Path:
        channel_dir = output_dir / "2001"
        channel_dir.mkdir(exist_ok=True)
        output_path = channel_dir / "123.stdout"
        output_path.write_bytes(data)
        return output_path

    def assert_read_failed(self, result, event: OutputReadEvent) -> None:
        self.assertEqual(result.status, "READ_FAILED")
        self.assertEqual(result.bytes, 0)
        self.assertEqual(result.content, "")
        self.assertEqual(result.content_encoding, "utf-8")
        self.assertFalse(result.eof)
        self.assertTrue(result.error_message)
        self.assertEqual(result.prev_seq, event.seq)
        self.assertEqual(result.target_seq, event.request.target_seq)
        self.assertEqual(result.stream, event.request.stream)
        self.assertEqual(result.offset, event.request.offset)
        self.assertEqual(result.nbytes, event.request.nbytes)
        dumps_payload(output_read_result_input_to_dict(result))

    def test_run_stops_worker_after_runtime_error(self) -> None:
        worker = CmdWorker(
            CmdWorkerConfig(
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
        worker = CmdWorker(
            CmdWorkerConfig(
                open_event=OpenEventConfig(addr="127.0.0.1:9527"),
                principal=30001,
                token="token",
                channel_ids=(2001,),
                output_dir=Path("cmd-worker-output"),
            ),
            FakeClient(),
        )
        task = SimpleNamespace(deadline_ms=10**15, channel_id=2001, seq=1)
        failure = RuntimeError("wait failed")
        with patch.object(worker.runner, "wait", side_effect=failure):
            worker._wait_task(task)
        with self.assertRaisesRegex(RuntimeError, "failed to wait for command") as raised:
            worker._drain_completions()
        self.assertIs(raised.exception.__cause__, failure)

    def test_output_read_returns_the_exact_requested_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = b"abcdefghij"
            self.write_output(Path(tmp), data)
            worker = self.make_worker(Path(tmp))
            for offset, nbytes in ((0, 3), (3, 3), (7, 3), (0, 10)):
                with self.subTest(offset=offset, nbytes=nbytes):
                    result = worker._build_output_read_result(self.read_event(offset, nbytes))
                    self.assertEqual(result.status, "SUCCESS")
                    self.assertEqual(result.bytes, nbytes)
                    self.assertEqual(result.content.encode("utf-8"), data[offset:offset + nbytes])
                    self.assertEqual(result.eof, offset + nbytes == len(data))
                    self.assertEqual(result.offset, offset)
                    self.assertEqual(result.nbytes, nbytes)
                    self.assertLessEqual(len(dumps_payload(output_read_result_input_to_dict(result))), 1024)

    def test_output_read_range_beyond_eof_never_returns_partial_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worker = self.make_worker(Path(tmp))
            for data, offset, nbytes in (
                (b"abcdef", 4, 3),
                (b"abcdef", 6, 1),
                (b"abcdef", 7, 1),
                (b"", 0, 1),
            ):
                with self.subTest(data=data, offset=offset, nbytes=nbytes):
                    self.write_output(Path(tmp), data)
                    event = self.read_event(offset, nbytes)
                    self.assert_read_failed(worker._build_output_read_result(event), event)

    def test_output_read_missing_file_remains_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worker = self.make_worker(Path(tmp))
            result = worker._build_output_read_result(self.read_event(0, 1))
            self.assertEqual(result.status, "NOT_FOUND")
            self.assertEqual(result.bytes, 0)
            self.assertEqual(result.content, "")
            self.assertEqual(result.content_encoding, "utf-8")
            self.assertTrue(result.eof)

    def test_output_read_short_read_discards_partial_content(self) -> None:
        worker = self.make_worker(Path("unused-output"))
        event = self.read_event(0, 6)
        handle = MagicMock()
        handle.__enter__.return_value = handle
        handle.read.return_value = b"abc"
        with patch.object(Path, "open", return_value=handle), patch(
            "openevent.cmd_worker.worker.os.fstat", return_value=SimpleNamespace(st_size=6),
        ):
            result = worker._build_output_read_result(event)
        handle.read.assert_called_once_with(6)
        self.assert_read_failed(result, event)

    def test_output_read_encoding_or_envelope_over_limit_returns_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worker = self.make_worker(Path(tmp))
            for name, data in (
                ("json_escape", b"\x00" * 256),
                ("base64", b"\xff" * 768),
                ("envelope", b"x" * 1024),
            ):
                with self.subTest(encoding=name):
                    self.write_output(Path(tmp), data)
                    event = self.read_event(0, len(data))
                    worker._handle_output_read_event(event)
                    payload = worker.openevent_client.payloads[-1]
                    result = dict_to_model(loads_payload(payload))
                    self.assert_read_failed(result, event)
                    self.assertLessEqual(len(payload), 1024)

    def test_output_read_huge_range_is_rejected_before_reading(self) -> None:
        worker = self.make_worker(Path("unused-output"))
        size = 2**63 - 1
        event = self.read_event(0, size)
        handle = MagicMock()
        handle.__enter__.return_value = handle
        with patch.object(Path, "open", return_value=handle), patch(
            "openevent.cmd_worker.worker.os.fstat", return_value=SimpleNamespace(st_size=size),
        ):
            result = worker._build_output_read_result(event)
        handle.read.assert_not_called()
        self.assert_read_failed(result, event)

    def test_output_read_failure_does_not_stop_following_requests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.write_output(Path(tmp), b"hello")
            worker = self.make_worker(Path(tmp))
            worker.publisher.publish_output_read_result = Mock()
            failed_event = self.read_event(0, 6)
            worker._handle_output_read_event(failed_event)
            worker._handle_output_read_event(self.read_event(0, 5, seq=125))
            calls = worker.publisher.publish_output_read_result.call_args_list
            self.assertEqual(len(calls), 2)
            self.assert_read_failed(calls[0].args[2], failed_event)
            self.assertEqual(calls[1].args[:2], (2001, 1001))
            self.assertEqual(calls[1].args[2].status, "SUCCESS")
            self.assertEqual(calls[1].args[2].content, "hello")
            self.assertEqual(calls[1].args[2].bytes, 5)
            self.assertFalse(worker.stopping)

if __name__ == "__main__":
    unittest.main()
