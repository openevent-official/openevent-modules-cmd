from __future__ import annotations

import base64
import binascii
import json
import time
from pathlib import PurePosixPath
from typing import Any

from .errors import CmdSDKError
from .model import (
    KIND_KILL_REQUEST,
    KIND_KILL_RESULT,
    KIND_OUTPUT_READ_REQUEST,
    KIND_OUTPUT_READ_RESULT,
    KIND_RUN_ACCEPTED,
    KIND_RUN_REQUEST,
    KIND_RUN_RESULT,
    KILL_STATUSES,
    OUTPUT_READ_STATUSES,
    OUTPUT_STREAMS,
    RUN_STATUSES,
    CmdKillRequest,
    CmdKillRequestInput,
    CmdKillResult,
    CmdKillResultInput,
    CmdOutput,
    CmdOutputReadRequest,
    CmdOutputReadRequestInput,
    CmdOutputReadResult,
    CmdOutputReadResultInput,
    CmdRunAccepted,
    CmdRunRequest,
    CmdRunRequestInput,
    CmdRunResult,
    CmdRunResultInput,
)

COMMON_KINDS = frozenset(
    {
        KIND_RUN_REQUEST,
        KIND_RUN_ACCEPTED,
        KIND_RUN_RESULT,
        KIND_KILL_REQUEST,
        KIND_KILL_RESULT,
        KIND_OUTPUT_READ_REQUEST,
        KIND_OUTPUT_READ_RESULT,
    }
)
def now_ms() -> int:
    return int(time.time() * 1000)


def dumps_payload(data: dict[str, Any]) -> bytes:
    validate_payload_dict(data)
    try:
        return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CmdSDKError("INVALID_JSON", "payload cannot be encoded as JSON") from exc


def loads_payload(payload: bytes) -> dict[str, Any]:
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CmdSDKError("INVALID_JSON", "payload is not UTF-8 JSON") from exc
    return validate_payload_dict(data)


def identify_kind(payload: bytes) -> str | None:
    try:
        data = json.loads(payload.decode("utf-8"))
    except Exception:
        return None
    if isinstance(data, dict) and isinstance(data.get("kind"), str):
        return data["kind"]
    return None


def run_request_input_to_dict(req: CmdRunRequestInput, ts_ms: int | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "kind": KIND_RUN_REQUEST,
        "ts_ms": req.ts_ms if req.ts_ms is not None else ts_ms or now_ms(),
        "command": req.command,
    }
    if req.workdir is not None:
        data["workdir"] = req.workdir
    if req.timeout_ms is not None:
        data["timeout_ms"] = req.timeout_ms
    return data


def run_accepted_to_dict(prev_seq: int, ts_ms: int | None = None) -> dict[str, Any]:
    return {"kind": KIND_RUN_ACCEPTED, "prev_seq": prev_seq, "ts_ms": ts_ms if ts_ms is not None else now_ms()}


def run_result_input_to_dict(result: CmdRunResultInput) -> dict[str, Any]:
    data: dict[str, Any] = {
        "kind": KIND_RUN_RESULT,
        "prev_seq": result.prev_seq,
        "ts_ms": result.ts_ms if result.ts_ms is not None else now_ms(),
        "status": result.status,
        "finished_at_ms": result.finished_at_ms,
        "stdout": output_to_dict(result.stdout),
        "stderr": output_to_dict(result.stderr),
    }
    if result.timeout_ms is not None:
        data["timeout_ms"] = result.timeout_ms
    if result.started_at_ms is not None:
        data["started_at_ms"] = result.started_at_ms
    if result.exit_code is not None:
        data["exit_code"] = result.exit_code
    if result.error_message is not None:
        data["error_message"] = result.error_message
    return data


def kill_request_input_to_dict(req: CmdKillRequestInput, ts_ms: int | None = None) -> dict[str, Any]:
    return {"kind": KIND_KILL_REQUEST, "ts_ms": req.ts_ms if req.ts_ms is not None else ts_ms or now_ms(), "target_seq": req.target_seq}


def kill_result_input_to_dict(result: CmdKillResultInput) -> dict[str, Any]:
    data: dict[str, Any] = {
        "kind": KIND_KILL_RESULT,
        "prev_seq": result.prev_seq,
        "ts_ms": result.ts_ms if result.ts_ms is not None else now_ms(),
        "status": result.status,
    }
    if result.target_seq is not None:
        data["target_seq"] = result.target_seq
    if result.error_message is not None:
        data["error_message"] = result.error_message
    return data


def output_read_request_input_to_dict(req: CmdOutputReadRequestInput, ts_ms: int | None = None) -> dict[str, Any]:
    return {
        "kind": KIND_OUTPUT_READ_REQUEST,
        "ts_ms": req.ts_ms if req.ts_ms is not None else ts_ms or now_ms(),
        "target_seq": req.target_seq,
        "stream": req.stream,
        "offset": req.offset,
        "nbytes": req.nbytes,
    }


def output_read_result_input_to_dict(result: CmdOutputReadResultInput) -> dict[str, Any]:
    data: dict[str, Any] = {
        "kind": KIND_OUTPUT_READ_RESULT,
        "prev_seq": result.prev_seq,
        "ts_ms": result.ts_ms if result.ts_ms is not None else now_ms(),
        "status": result.status,
        "bytes": result.bytes,
        "eof": result.eof,
        "content_encoding": result.content_encoding,
        "content": result.content,
    }
    for key in ("target_seq", "stream", "offset", "nbytes", "error_message"):
        value = getattr(result, key)
        if value is not None:
            data[key] = value
    return data


def output_to_dict(output: CmdOutput) -> dict[str, Any]:
    data: dict[str, Any] = {"file": output.file, "bytes": output.bytes}
    if output.content is not None:
        data["content"] = output.content
        data["content_encoding"] = output.content_encoding
    return data


def dict_to_model(data: dict[str, Any]):
    validate_payload_dict(data)
    kind = data["kind"]
    if kind == KIND_RUN_REQUEST:
        return CmdRunRequest(
            command=data["command"],
            workdir=data.get("workdir"),
            timeout_ms=data.get("timeout_ms"),
            ts_ms=data["ts_ms"],
        )
    if kind == KIND_RUN_ACCEPTED:
        return CmdRunAccepted(prev_seq=data["prev_seq"], ts_ms=data["ts_ms"])
    if kind == KIND_RUN_RESULT:
        return CmdRunResult(
            prev_seq=data["prev_seq"],
            ts_ms=data["ts_ms"],
            status=data["status"],
            timeout_ms=data.get("timeout_ms"),
            started_at_ms=data.get("started_at_ms"),
            finished_at_ms=data["finished_at_ms"],
            exit_code=data.get("exit_code"),
            stdout=dict_to_output(data["stdout"]),
            stderr=dict_to_output(data["stderr"]),
            error_message=data.get("error_message"),
        )
    if kind == KIND_KILL_REQUEST:
        return CmdKillRequest(target_seq=data["target_seq"], ts_ms=data["ts_ms"])
    if kind == KIND_KILL_RESULT:
        return CmdKillResult(prev_seq=data["prev_seq"], target_seq=data.get("target_seq"), ts_ms=data["ts_ms"], status=data["status"], error_message=data.get("error_message"))
    if kind == KIND_OUTPUT_READ_REQUEST:
        return CmdOutputReadRequest(
            target_seq=data["target_seq"],
            stream=data["stream"],
            offset=data["offset"],
            nbytes=data["nbytes"],
            ts_ms=data["ts_ms"],
        )
    if kind == KIND_OUTPUT_READ_RESULT:
        return CmdOutputReadResult(
            prev_seq=data["prev_seq"],
            ts_ms=data["ts_ms"],
            status=data["status"],
            target_seq=data.get("target_seq"),
            stream=data.get("stream"),
            offset=data.get("offset"),
            nbytes=data.get("nbytes"),
            bytes=data["bytes"],
            eof=data["eof"],
            content_encoding=data["content_encoding"],
            content=data["content"],
            error_message=data.get("error_message"),
        )
    raise CmdSDKError("INVALID_KIND", "unknown cmd kind")


def dict_to_output(data: dict[str, Any]) -> CmdOutput:
    return CmdOutput(file=data["file"], bytes=data["bytes"], content_encoding=data.get("content_encoding"), content=data.get("content"))


def validate_payload_dict(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise CmdSDKError("INVALID_PAYLOAD", "payload must be a JSON object")
    kind = data.get("kind")
    if kind not in COMMON_KINDS:
        raise CmdSDKError("INVALID_KIND", "kind is missing or invalid")
    _require_ts(data)
    if kind == KIND_RUN_REQUEST:
        _reject_unknown(data, {"kind", "ts_ms", "command", "workdir", "timeout_ms"})
        _require_non_empty_str(data, "command")
        if "workdir" in data:
            workdir = _require_non_empty_str(data, "workdir")
            if not workdir.startswith("/"):
                raise CmdSDKError("INVALID_WORKDIR", "workdir must be absolute")
        if "timeout_ms" in data:
            _require_positive_int(data, "timeout_ms")
    elif kind == KIND_RUN_ACCEPTED:
        _reject_unknown(data, {"kind", "ts_ms", "prev_seq"})
        _require_positive_int(data, "prev_seq")
    elif kind == KIND_RUN_RESULT:
        _reject_unknown(
            data,
            {
                "kind",
                "ts_ms",
                "prev_seq",
                "status",
                "exit_code",
                "timeout_ms",
                "started_at_ms",
                "finished_at_ms",
                "stdout",
                "stderr",
                "error_message",
            },
        )
        _require_positive_int(data, "prev_seq")
        status = _require_enum(data, "status", RUN_STATUSES)
        _require_non_negative_int(data, "finished_at_ms")
        if "timeout_ms" in data:
            _require_positive_int(data, "timeout_ms")
        elif status != "REJECTED":
            raise CmdSDKError("MISSING_TIMEOUT", "timeout_ms is required unless status is REJECTED")
        if "started_at_ms" in data:
            _require_non_negative_int(data, "started_at_ms")
        if "exit_code" in data and not isinstance(data["exit_code"], int):
            raise CmdSDKError("INVALID_EXIT_CODE", "exit_code must be an integer")
        if status in {"SUCCESS", "FAILED"}:
            if "exit_code" not in data:
                raise CmdSDKError("MISSING_EXIT_CODE", "exit_code is required for SUCCESS or FAILED")
        elif "exit_code" in data:
            raise CmdSDKError("INVALID_EXIT_CODE", "exit_code is only valid for SUCCESS or FAILED")
        if status in {"REJECTED", "START_FAILED", "RECOVERY_FAILED"}:
            _require_non_empty_str(data, "error_message")
        if "stdout" not in data or "stderr" not in data:
            raise CmdSDKError("MISSING_OUTPUT", "stdout and stderr are required")
        validate_output(data["stdout"], "stdout")
        validate_output(data["stderr"], "stderr")
    elif kind == KIND_KILL_REQUEST:
        _reject_unknown(data, {"kind", "ts_ms", "target_seq"})
        _require_positive_int(data, "target_seq")
    elif kind == KIND_KILL_RESULT:
        _reject_unknown(data, {"kind", "ts_ms", "prev_seq", "target_seq", "status", "error_message"})
        _require_positive_int(data, "prev_seq")
        status = _require_enum(data, "status", KILL_STATUSES)
        if "target_seq" in data:
            _require_positive_int(data, "target_seq")
        if status == "REJECTED":
            _require_non_empty_str(data, "error_message")
    elif kind == KIND_OUTPUT_READ_REQUEST:
        _reject_unknown(data, {"kind", "ts_ms", "target_seq", "stream", "offset", "nbytes"})
        _require_positive_int(data, "target_seq")
        _require_enum(data, "stream", OUTPUT_STREAMS)
        _require_non_negative_int(data, "offset")
        _require_positive_int(data, "nbytes")
    elif kind == KIND_OUTPUT_READ_RESULT:
        _reject_unknown(
            data,
            {
                "kind",
                "ts_ms",
                "prev_seq",
                "target_seq",
                "status",
                "stream",
                "offset",
                "nbytes",
                "bytes",
                "eof",
                "content_encoding",
                "content",
                "error_message",
            },
        )
        _require_positive_int(data, "prev_seq")
        status = _require_enum(data, "status", OUTPUT_READ_STATUSES)
        if "target_seq" in data:
            _require_positive_int(data, "target_seq")
        if "stream" in data:
            _require_enum(data, "stream", OUTPUT_STREAMS)
        if "offset" in data:
            _require_non_negative_int(data, "offset")
        if "nbytes" in data:
            _require_positive_int(data, "nbytes")
        _require_non_negative_int(data, "bytes")
        if not isinstance(data.get("eof"), bool):
            raise CmdSDKError("INVALID_EOF", "eof must be a boolean")
        _require_enum(data, "content_encoding", {"utf-8", "base64"})
        if not isinstance(data.get("content"), str):
            raise CmdSDKError("INVALID_CONTENT", "content must be a string")
        _validate_content_size(data["content_encoding"], data["content"], data["bytes"], "content")
        if status == "NOT_FOUND":
            _require_empty_output_read_result(data, eof=True, status=status)
        if status in {"READ_FAILED", "REJECTED"}:
            _require_non_empty_str(data, "error_message")
            _require_empty_output_read_result(data, eof=status == "REJECTED", status=status)
    return data


def validate_output(data: Any, field: str) -> None:
    if not isinstance(data, dict):
        raise CmdSDKError("INVALID_OUTPUT", f"{field} must be an object")
    _reject_unknown(data, {"file", "bytes", "content_encoding", "content"})
    file_value = _require_non_empty_str(data, "file")
    if PurePosixPath(file_value).is_absolute() or ".." in PurePosixPath(file_value).parts:
        raise CmdSDKError("INVALID_OUTPUT_FILE", f"{field}.file must be relative")
    byte_count = _require_non_negative_int(data, "bytes")
    has_content = "content" in data
    if byte_count <= 1024 and not has_content:
        raise CmdSDKError("MISSING_OUTPUT_CONTENT", f"{field}.content is required when bytes <= 1024")
    if byte_count > 1024 and has_content:
        raise CmdSDKError("INVALID_OUTPUT_CONTENT", f"{field}.content must be absent when bytes > 1024")
    if has_content:
        _require_enum(data, "content_encoding", {"utf-8", "base64"})
        if not isinstance(data["content"], str):
            raise CmdSDKError("INVALID_OUTPUT_CONTENT", f"{field}.content must be a string")
        _validate_content_size(data["content_encoding"], data["content"], byte_count, f"{field}.content")
    elif "content_encoding" in data:
        raise CmdSDKError("INVALID_OUTPUT_ENCODING", f"{field}.content_encoding requires content")


def _require_empty_output_read_result(data: dict[str, Any], eof: bool, status: str) -> None:
    if data["bytes"] != 0 or data["content"] != "" or data["content_encoding"] != "utf-8" or data["eof"] is not eof:
        raise CmdSDKError("INVALID_OUTPUT_READ_RESULT", f"{status} must return an empty utf-8 content body")


def _validate_content_size(encoding: str, content: str, expected_bytes: int, field: str) -> None:
    if encoding == "utf-8":
        actual_bytes = len(content.encode("utf-8"))
    else:
        try:
            actual_bytes = len(base64.b64decode(content.encode("ascii"), validate=True))
        except (UnicodeEncodeError, binascii.Error) as exc:
            raise CmdSDKError("INVALID_CONTENT", f"{field} must be valid base64") from exc
    if actual_bytes != expected_bytes:
        raise CmdSDKError("INVALID_CONTENT_SIZE", f"{field} byte length must match bytes")


def _require_ts(data: dict[str, Any]) -> None:
    _require_non_negative_int(data, "ts_ms")


def _reject_unknown(data: dict[str, Any], allowed: set[str]) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise CmdSDKError("UNKNOWN_FIELD", f"unknown fields: {', '.join(sorted(unknown))}")


def _require_non_empty_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise CmdSDKError("INVALID_FIELD", f"{key} must be a non-empty string")
    return value


def _require_enum(data: dict[str, Any], key: str, allowed: frozenset[str] | set[str]) -> str:
    value = data.get(key)
    if not isinstance(value, str) or value not in allowed:
        raise CmdSDKError("INVALID_FIELD", f"{key} is invalid")
    return value


def _require_positive_int(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise CmdSDKError("INVALID_FIELD", f"{key} must be a positive integer")
    return value


def _require_non_negative_int(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise CmdSDKError("INVALID_FIELD", f"{key} must be a non-negative integer")
    return value
