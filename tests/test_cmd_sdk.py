from __future__ import annotations

import base64
import json
import unittest
from dataclasses import fields
from types import SimpleNamespace
from unittest.mock import patch

from openevent.cmd_sdk import (
    CmdKillResult,
    CmdKillResultInput,
    CmdOutput,
    CmdOutputReadRequestInput,
    CmdOutputReadResult,
    CmdOutputReadResultInput,
    CmdRunRequestInput,
    CmdRunResult,
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

    def test_result_round_trips_cover_all_current_statuses(self) -> None:
        for data in _result_payloads():
            with self.subTest(kind=data["kind"], status=data["status"]):
                self.assertEqual(loads_payload(dumps_payload(data)), data)
                parsed = parse_message(SimpleNamespace(
                    seq=3, channel_id=2001, principal=30001, recipients=[1001],
                    object_keys=[], payload=dumps_payload(data),
                ))
                self.assertEqual(parsed.payload.status, data["status"])
                self.assertEqual(parsed.payload.prev_seq, data["prev_seq"])
                self.assertEqual(parsed.payload.ts_ms, data["ts_ms"])
                for field in _REQUIRED_RESULT_FIELDS[data["kind"]]:
                    self.assertEqual(getattr(parsed.payload, field), data[field])

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

    def test_run_result_requires_error_message_for_recovery_failed(self) -> None:
        result = CmdRunResultInput(
            prev_seq=1,
            status="RECOVERY_FAILED",
            timeout_ms=1000,
            stdout=CmdOutput(file="1/1.stdout", bytes=0, content_encoding="utf-8", content=""),
            stderr=CmdOutput(file="1/1.stderr", bytes=0, content_encoding="utf-8", content=""),
        )
        with self.assertRaises(CmdSDKError):
            dumps_payload(run_result_input_to_dict(result))

    def test_run_result_round_trip_uses_one_generation_time(self) -> None:
        result = CmdRunResultInput(
            prev_seq=1, status="SUCCESS", timeout_ms=1000, exit_code=0,
            started_at_ms=100, ts_ms=123,
            stdout=CmdOutput(file="2001/1.stdout", bytes=0, content_encoding="utf-8", content=""),
            stderr=CmdOutput(file="2001/1.stderr", bytes=0, content_encoding="utf-8", content=""),
        )
        payload = dumps_payload(run_result_input_to_dict(result))
        self.assertEqual(loads_payload(payload), {
            "kind": "cmd.run.result", "prev_seq": 1, "ts_ms": 123,
            "status": "SUCCESS", "timeout_ms": 1000, "exit_code": 0,
            "started_at_ms": 100,
            "stdout": {"file": "2001/1.stdout", "bytes": 0, "content_encoding": "utf-8", "content": ""},
            "stderr": {"file": "2001/1.stderr", "bytes": 0, "content_encoding": "utf-8", "content": ""},
        })
        parsed = parse_message(SimpleNamespace(
            seq=2, channel_id=2001, principal=30001, recipients=[1001],
            object_keys=[], payload=payload,
        ))
        self.assertEqual(parsed.payload.ts_ms, 123)
        self.assertEqual(parsed.payload.started_at_ms, 100)

    def test_results_require_request_fields_for_every_status(self) -> None:
        for data in _result_payloads():
            for field in _REQUIRED_RESULT_FIELDS[data["kind"]]:
                with self.subTest(kind=data["kind"], status=data["status"], missing=field):
                    missing_field = {key: value for key, value in data.items() if key != field}
                    with self.assertRaises(CmdSDKError):
                        dumps_payload(missing_field)
                    raw = json.dumps(missing_field).encode("utf-8")
                    with self.assertRaises(CmdSDKError):
                        loads_payload(raw)
                    with self.assertRaises(CmdSDKError):
                        parse_message(SimpleNamespace(
                            seq=3, channel_id=2001, principal=30001, recipients=[1001],
                            object_keys=[], payload=raw,
                        ))

    def test_result_models_require_protocol_fields_at_construction(self) -> None:
        models = {
            "cmd.run.result": (CmdRunResultInput, CmdRunResult),
            "cmd.kill.result": (CmdKillResultInput, CmdKillResult),
            "cmd.output.read.result": (CmdOutputReadResultInput, CmdOutputReadResult),
        }
        for data in _result_payloads():
            parsed = codec.dict_to_model(data)
            values = {field.name: getattr(parsed, field.name) for field in fields(parsed) if field.name != "kind"}
            for model in models[data["kind"]]:
                complete = model(**values)
                for field in _REQUIRED_RESULT_FIELDS[data["kind"]]:
                    with self.subTest(model=model.__name__, status=data["status"], missing=field):
                        self.assertEqual(getattr(complete, field), data[field])
                        missing_field = {key: value for key, value in values.items() if key != field}
                        with self.assertRaisesRegex(TypeError, field):
                            model(**missing_field)

    def test_kill_result_has_no_error_message_field(self) -> None:
        for status in ("KILL_SENT", "REMOVED_PENDING", "NOT_RUNNING"):
            with self.subTest(status=status):
                values = dict(prev_seq=2, target_seq=1, status=status, ts_ms=123)
                result = CmdKillResultInput(**values)
                data = kill_result_input_to_dict(result)
                self.assertNotIn("error_message", data)
                parsed = codec.dict_to_model(loads_payload(dumps_payload(data)))
                self.assertFalse(hasattr(parsed, "error_message"))
                for model in (CmdKillResultInput, CmdKillResult):
                    with self.assertRaisesRegex(TypeError, "error_message"):
                        model(**values, error_message="unused")
                for error_message in (None, "unused"):
                    unknown_field = {**data, "error_message": error_message}
                    with self.assertRaises(CmdSDKError) as raised:
                        dumps_payload(unknown_field)
                    self.assertEqual(raised.exception.code, "UNKNOWN_FIELD")
                    with self.assertRaises(CmdSDKError) as raised:
                        parse_message(SimpleNamespace(
                            seq=3, channel_id=2001, principal=30001, recipients=[1001],
                            object_keys=[], payload=json.dumps(unknown_field).encode("utf-8"),
                        ))
                    self.assertEqual(raised.exception.code, "UNKNOWN_FIELD")

    def test_output_content_size_must_match_bytes(self) -> None:
        result = CmdRunResultInput(
            prev_seq=1,
            status="SUCCESS",
            timeout_ms=1000,
            exit_code=0,
            stdout=CmdOutput(file="1/1.stdout", bytes=2, content_encoding="utf-8", content="x"),
            stderr=CmdOutput(file="1/1.stderr", bytes=0, content_encoding="utf-8", content=""),
        )
        with self.assertRaises(CmdSDKError):
            dumps_payload(run_result_input_to_dict(result))

    def test_output_read_success_returns_exact_requested_byte_count(self) -> None:
        for encoding, content, byte_count in (("utf-8", "你", 3), ("base64", "/wAB", 3)):
            for eof in (False, True):
                with self.subTest(encoding=encoding, eof=eof):
                    data = output_read_result_input_to_dict(CmdOutputReadResultInput(
                        prev_seq=2, target_seq=1, stream="stdout", offset=11, nbytes=byte_count,
                        status="SUCCESS", bytes=byte_count, eof=eof, content_encoding=encoding,
                        content=content, ts_ms=123,
                    ))
                    payload = dumps_payload(data)
                    self.assertEqual(loads_payload(payload), data)
                    parsed = parse_message(SimpleNamespace(
                        seq=3, channel_id=2001, principal=30001, recipients=[1001],
                        object_keys=[], payload=payload,
                    ))
                    self.assertEqual(parsed.payload.bytes, parsed.payload.nbytes)
                    self.assertEqual(parsed.payload.content, content)
                    self.assertEqual(parsed.payload.eof, eof)

    def test_output_read_success_rejects_empty_short_and_long_results(self) -> None:
        for byte_count in (0, 2, 4):
            for encoding in ("utf-8", "base64"):
                for eof in (False, True):
                    with self.subTest(bytes=byte_count, encoding=encoding, eof=eof):
                        raw = b"x" * byte_count
                        content = raw.decode("utf-8") if encoding == "utf-8" else base64.b64encode(raw).decode("ascii")
                        data = output_read_result_input_to_dict(CmdOutputReadResultInput(
                            prev_seq=2, target_seq=1, stream="stdout", offset=11, nbytes=3,
                            status="SUCCESS", bytes=byte_count, eof=eof, content_encoding=encoding,
                            content=content, ts_ms=123,
                        ))
                        with self.assertRaisesRegex(CmdSDKError, "exactly nbytes bytes"):
                            dumps_payload(data)
                        with self.assertRaisesRegex(CmdSDKError, "exactly nbytes bytes"):
                            parse_message(SimpleNamespace(
                                seq=3, channel_id=2001, principal=30001, recipients=[1001],
                                object_keys=[], payload=json.dumps(data).encode("utf-8"),
                            ))

    def test_output_read_failure_requires_empty_body_and_error(self) -> None:
        failure = output_read_result_input_to_dict(CmdOutputReadResultInput(
            prev_seq=2, target_seq=1, stream="stdout", offset=11, nbytes=3,
            status="READ_FAILED", bytes=0, eof=False, content_encoding="utf-8",
            content="", error_message="requested range is unavailable", ts_ms=123,
        ))
        self.assertEqual(loads_payload(dumps_payload(failure)), failure)
        for changes in (
            {"bytes": 1, "content": "x"},
            {"content_encoding": "base64"},
            {"eof": True},
            {"error_message": ""},
        ):
            with self.subTest(changes=changes):
                data = {**failure, **changes}
                with self.assertRaises(CmdSDKError):
                    dumps_payload(data)
                with self.assertRaises(CmdSDKError):
                    loads_payload(json.dumps(data).encode("utf-8"))
        failure.pop("error_message")
        with self.assertRaises(CmdSDKError):
            dumps_payload(failure)
        with self.assertRaises(CmdSDKError):
            loads_payload(json.dumps(failure).encode("utf-8"))

    def test_rejected_is_not_a_result_status(self) -> None:
        for data in _result_payloads():
            with self.subTest(kind=data["kind"], original_status=data["status"]):
                data["status"] = "REJECTED"
                with self.assertRaisesRegex(CmdSDKError, "status is invalid"):
                    dumps_payload(data)
                with self.assertRaisesRegex(CmdSDKError, "status is invalid"):
                    parse_message(SimpleNamespace(
                        seq=3, channel_id=2001, principal=30001, recipients=[1001],
                        object_keys=[], payload=json.dumps(data).encode("utf-8"),
                    ))

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


_REQUIRED_RESULT_FIELDS = {
    "cmd.run.result": ("timeout_ms",),
    "cmd.kill.result": ("target_seq",),
    "cmd.output.read.result": ("target_seq", "stream", "offset", "nbytes"),
}


def _result_payloads():
    for status in ("SUCCESS", "FAILED", "TIMEOUT", "KILLED", "START_FAILED", "RECOVERY_FAILED"):
        yield run_result_input_to_dict(CmdRunResultInput(
            prev_seq=1, status=status, timeout_ms=1000, ts_ms=123,
            exit_code=(0 if status == "SUCCESS" else 1) if status in {"SUCCESS", "FAILED"} else None,
            error_message="execution failed" if status in {"START_FAILED", "RECOVERY_FAILED"} else None,
            stdout=CmdOutput(file="2001/1.stdout", bytes=0, content_encoding="utf-8", content=""),
            stderr=CmdOutput(file="2001/1.stderr", bytes=0, content_encoding="utf-8", content=""),
        ))
    for status in ("KILL_SENT", "REMOVED_PENDING", "NOT_RUNNING"):
        yield kill_result_input_to_dict(CmdKillResultInput(
            prev_seq=2, target_seq=1, status=status, ts_ms=123,
        ))
    for status in ("SUCCESS", "NOT_FOUND", "READ_FAILED"):
        yield output_read_result_input_to_dict(CmdOutputReadResultInput(
            prev_seq=2, target_seq=1, stream="stdout", offset=0, nbytes=1,
            status=status, bytes=1 if status == "SUCCESS" else 0,
            content="x" if status == "SUCCESS" else "", content_encoding="utf-8",
            eof=status != "READ_FAILED", ts_ms=123,
            error_message="read failed" if status == "READ_FAILED" else None,
        ))


def _timeout_payloads(timeout_ms):
    return (
        run_request_input_to_dict(CmdRunRequestInput(command="true", timeout_ms=timeout_ms, ts_ms=1)),
        run_result_input_to_dict(CmdRunResultInput(
            prev_seq=1, status="TIMEOUT", timeout_ms=timeout_ms, ts_ms=2,
            stdout=CmdOutput(file="1/1.stdout", bytes=0, content_encoding="utf-8", content=""),
            stderr=CmdOutput(file="1/1.stderr", bytes=0, content_encoding="utf-8", content=""),
        )),
    )


if __name__ == "__main__":
    unittest.main()
