from __future__ import annotations

import base64
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from openevent.cmd_sdk import (
    CmdKillResultInput,
    CmdOutput,
    CmdOutputReadRequestInput,
    CmdOutputReadResultInput,
    CmdRunRequestInput,
    CmdRunResultInput,
    CmdSDKError,
    dumps_payload,
    loads_payload,
    parse_message,
)
from openevent.cmd_sdk import codec
from openevent.cmd_sdk.codec import kill_result_input_to_dict, output_read_request_input_to_dict, output_read_result_input_to_dict, run_request_input_to_dict, run_result_input_to_dict
from openevent.cmd_sdk.model import MAX_TIMEOUT_MS


class CmdSDKTest(unittest.TestCase):
    def test_run_request_round_trip(self) -> None:
        payload = dumps_payload(
            run_request_input_to_dict(
                CmdRunRequestInput(command="echo hello", workdir="/tmp", timeout_ms=1000, ts_ms=123)
            )
        )
        self.assertEqual(
            loads_payload(payload),
            {
                "kind": "cmd.run.request",
                "ts_ms": 123,
                "command": "echo hello",
                "workdir": "/tmp",
                "timeout_ms": 1000,
            },
        )

    def test_relative_workdir_is_rejected(self) -> None:
        with self.assertRaises(CmdSDKError):
            dumps_payload(run_request_input_to_dict(CmdRunRequestInput(command="pwd", workdir="relative", ts_ms=1)))

    def test_run_timeout_accepts_both_boundaries(self) -> None:
        for timeout_ms in (1, MAX_TIMEOUT_MS):
            for data in _timeout_payloads(timeout_ms):
                with self.subTest(kind=data["kind"], timeout_ms=timeout_ms):
                    parsed = loads_payload(dumps_payload(data))
                    self.assertEqual(parsed["timeout_ms"], timeout_ms)

    def test_run_timeout_rejects_out_of_range_and_non_integer_values(self) -> None:
        for timeout_ms in (-1, 0, MAX_TIMEOUT_MS + 1, 10**400, True, 1.5):
            for data in _timeout_payloads(timeout_ms):
                with self.subTest(kind=data["kind"], timeout_ms=timeout_ms):
                    with self.assertRaises(CmdSDKError):
                        dumps_payload(data)
                    with self.assertRaises(CmdSDKError):
                        loads_payload(json.dumps(data).encode("utf-8"))

    def test_output_read_request_round_trip(self) -> None:
        payload = dumps_payload(
            output_read_request_input_to_dict(
                CmdOutputReadRequestInput(
                    target_seq=10,
                    stream="stdout",
                    offset=0,
                    nbytes=4096,
                    ts_ms=123,
                )
            )
        )

        self.assertEqual(
            loads_payload(payload),
            {
                "kind": "cmd.output.read.request",
                "ts_ms": 123,
                "target_seq": 10,
                "stream": "stdout",
                "offset": 0,
                "nbytes": 4096,
            },
        )

    def test_kill_result_round_trip_uses_current_statuses(self) -> None:
        for status in ("KILL_SENT", "REMOVED_PENDING", "NOT_RUNNING", "REJECTED"):
            with self.subTest(status=status):
                result = CmdKillResultInput(
                    prev_seq=2, target_seq=1, status=status, ts_ms=123,
                    error_message="bad request" if status == "REJECTED" else None,
                )
                data = kill_result_input_to_dict(result)
                self.assertEqual(loads_payload(dumps_payload(data)), data)
                parsed = parse_message(SimpleNamespace(
                    seq=3, channel_id=2001, principal=30001, recipients=[1001],
                    object_keys=[], payload=dumps_payload(data),
                ))
                self.assertEqual(parsed.payload.status, status)
                self.assertEqual(parsed.payload.target_seq, 1)

    def test_kill_result_rejects_removed_statuses(self) -> None:
        for status in ("ALREADY_FINISHED", "NOT_FOUND"):
            with self.subTest(status=status):
                data = kill_result_input_to_dict(CmdKillResultInput(
                    prev_seq=2, target_seq=1, status=status, ts_ms=123,
                ))
                with self.assertRaises(CmdSDKError):
                    dumps_payload(data)
                with self.assertRaises(CmdSDKError):
                    loads_payload(json.dumps(data).encode("utf-8"))

    def test_output_read_still_accepts_not_found(self) -> None:
        result = CmdOutputReadResultInput(
            prev_seq=2, target_seq=1, stream="stdout", offset=0, nbytes=1,
            status="NOT_FOUND", bytes=0, eof=True, content_encoding="utf-8",
            content="", ts_ms=123,
        )
        data = output_read_result_input_to_dict(result)
        self.assertEqual(loads_payload(dumps_payload(data)), data)

    def test_run_result_requires_error_message_for_recovery_failed(self) -> None:
        result = CmdRunResultInput(
            prev_seq=1,
            status="RECOVERY_FAILED",
            timeout_ms=1000,
            finished_at_ms=2,
            stdout=CmdOutput(file="1/1.stdout", bytes=0, content_encoding="utf-8", content=""),
            stderr=CmdOutput(file="1/1.stderr", bytes=0, content_encoding="utf-8", content=""),
        )
        with self.assertRaises(CmdSDKError):
            dumps_payload(run_result_input_to_dict(result))

    def test_run_result_requires_timeout_unless_rejected(self) -> None:
        result = CmdRunResultInput(
            prev_seq=1,
            status="TIMEOUT",
            finished_at_ms=2,
            stdout=CmdOutput(file="1/1.stdout", bytes=0, content_encoding="utf-8", content=""),
            stderr=CmdOutput(file="1/1.stderr", bytes=0, content_encoding="utf-8", content=""),
        )
        with self.assertRaises(CmdSDKError):
            dumps_payload(run_result_input_to_dict(result))

    def test_output_content_size_must_match_bytes(self) -> None:
        result = CmdRunResultInput(
            prev_seq=1,
            status="SUCCESS",
            timeout_ms=1000,
            finished_at_ms=2,
            exit_code=0,
            stdout=CmdOutput(file="1/1.stdout", bytes=2, content_encoding="utf-8", content="x"),
            stderr=CmdOutput(file="1/1.stderr", bytes=0, content_encoding="utf-8", content=""),
        )
        with self.assertRaises(CmdSDKError):
            dumps_payload(run_result_input_to_dict(result))

    def test_output_read_rejected_must_be_empty(self) -> None:
        result = CmdOutputReadResultInput(
            prev_seq=1,
            status="REJECTED",
            bytes=1,
            eof=True,
            content_encoding="utf-8",
            content="x",
            error_message="bad request",
        )
        with self.assertRaises(CmdSDKError):
            dumps_payload(output_read_result_input_to_dict(result))

    def test_unknown_field_is_rejected(self) -> None:
        with self.assertRaises(CmdSDKError):
            loads_payload(b'{"kind":"cmd.kill.request","ts_ms":1,"target_seq":2,"extra":true}')

    def test_request_id_is_rejected_as_an_unknown_field(self) -> None:
        with self.assertRaises(CmdSDKError):
            loads_payload(b'{"kind":"cmd.run.request","request_id":"old","ts_ms":1,"command":"true"}')

    def test_parse_message_validates_binary_output_once(self) -> None:
        raw = b"\x00\xff\x80" * 2048
        result = CmdOutputReadResultInput(
            prev_seq=10, target_seq=9, stream="stdout", offset=0, nbytes=len(raw),
            status="SUCCESS", bytes=len(raw), eof=True, content_encoding="base64",
            content=base64.b64encode(raw).decode("ascii"), ts_ms=123,
        )
        message = SimpleNamespace(
            seq=11, channel_id=2001, principal=30001, recipients=[1001],
            object_keys=[],
            payload=dumps_payload(output_read_result_input_to_dict(result)),
        )
        with patch.object(codec, "validate_payload_dict", wraps=codec.validate_payload_dict) as validate:
            parsed = parse_message(message)
        validate.assert_called_once()
        self.assertEqual((parsed.seq, parsed.channel_id, parsed.principal, parsed.recipients),
                         (11, 2001, 30001, (1001,)))
        self.assertEqual(parsed.payload.content_encoding, "base64")
        self.assertEqual(parsed.payload.bytes, len(raw))
        self.assertEqual(base64.b64decode(parsed.payload.content), raw)


def _timeout_payloads(timeout_ms):
    return (
        run_request_input_to_dict(CmdRunRequestInput(command="true", timeout_ms=timeout_ms, ts_ms=1)),
        run_result_input_to_dict(CmdRunResultInput(
            prev_seq=1, status="TIMEOUT", timeout_ms=timeout_ms, finished_at_ms=2, ts_ms=2,
            stdout=CmdOutput(file="1/1.stdout", bytes=0, content_encoding="utf-8", content=""),
            stderr=CmdOutput(file="1/1.stderr", bytes=0, content_encoding="utf-8", content=""),
        )),
    )


if __name__ == "__main__":
    unittest.main()
