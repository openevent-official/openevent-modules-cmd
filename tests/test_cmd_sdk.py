from __future__ import annotations

import unittest

from openevent.cmd_sdk import (
    CmdOutput,
    CmdOutputReadResultInput,
    CmdRunRequestInput,
    CmdRunResultInput,
    CmdSDKError,
    dumps_payload,
    loads_payload,
)
from openevent.cmd_sdk.codec import output_read_result_input_to_dict, run_request_input_to_dict, run_result_input_to_dict


class CmdSDKTest(unittest.TestCase):
    def test_run_request_round_trip(self) -> None:
        payload = dumps_payload(
            run_request_input_to_dict(
                CmdRunRequestInput(command="echo hello", workdir="/tmp", timeout_ms=1000, ts_ms=123)
            )
        )
        self.assertEqual(
            loads_payload(payload),
            {"kind": "cmd.run.request", "ts_ms": 123, "command": "echo hello", "workdir": "/tmp", "timeout_ms": 1000},
        )

    def test_relative_workdir_is_rejected(self) -> None:
        with self.assertRaises(CmdSDKError):
            dumps_payload(run_request_input_to_dict(CmdRunRequestInput(command="pwd", workdir="relative", ts_ms=1)))

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


if __name__ == "__main__":
    unittest.main()
