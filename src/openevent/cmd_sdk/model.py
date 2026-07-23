from __future__ import annotations

from dataclasses import dataclass


KIND_RUN_REQUEST = "cmd.run.request"
KIND_RUN_ACCEPTED = "cmd.run.accepted"
KIND_RUN_RESULT = "cmd.run.result"
KIND_KILL_REQUEST = "cmd.kill.request"
KIND_KILL_RESULT = "cmd.kill.result"
KIND_OUTPUT_READ_REQUEST = "cmd.output.read.request"
KIND_OUTPUT_READ_RESULT = "cmd.output.read.result"

RUN_STATUSES = frozenset(
    {
        "SUCCESS",
        "FAILED",
        "TIMEOUT",
        "KILLED",
        "START_FAILED",
        "RECOVERY_FAILED",
        "REJECTED",
    }
)
KILL_STATUSES = frozenset({"KILL_SENT", "REMOVED_PENDING", "ALREADY_FINISHED", "NOT_FOUND", "REJECTED"})
OUTPUT_READ_STATUSES = frozenset({"SUCCESS", "NOT_FOUND", "READ_FAILED", "REJECTED"})
OUTPUT_STREAMS = frozenset({"stdout", "stderr"})


@dataclass(frozen=True)
class CmdRunRequestInput:
    command: str
    workdir: str | None = None
    timeout_ms: int | None = None
    ts_ms: int | None = None


@dataclass(frozen=True)
class CmdRunRequest:
    command: str
    ts_ms: int
    workdir: str | None = None
    timeout_ms: int | None = None
    kind: str = KIND_RUN_REQUEST


@dataclass(frozen=True)
class CmdRunAccepted:
    prev_seq: int
    ts_ms: int
    kind: str = KIND_RUN_ACCEPTED


@dataclass(frozen=True)
class CmdOutput:
    file: str
    bytes: int
    content_encoding: str | None = None
    content: str | None = None


@dataclass(frozen=True)
class CmdRunResultInput:
    prev_seq: int
    status: str
    stdout: CmdOutput
    stderr: CmdOutput
    finished_at_ms: int
    ts_ms: int | None = None
    timeout_ms: int | None = None
    started_at_ms: int | None = None
    exit_code: int | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class CmdRunResult:
    prev_seq: int
    ts_ms: int
    status: str
    stdout: CmdOutput
    stderr: CmdOutput
    finished_at_ms: int
    timeout_ms: int | None = None
    started_at_ms: int | None = None
    exit_code: int | None = None
    error_message: str | None = None
    kind: str = KIND_RUN_RESULT


@dataclass(frozen=True)
class CmdKillRequestInput:
    target_seq: int
    ts_ms: int | None = None


@dataclass(frozen=True)
class CmdKillRequest:
    target_seq: int
    ts_ms: int
    kind: str = KIND_KILL_REQUEST


@dataclass(frozen=True)
class CmdKillResultInput:
    prev_seq: int
    status: str
    ts_ms: int | None = None
    target_seq: int | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class CmdKillResult:
    prev_seq: int
    ts_ms: int
    status: str
    target_seq: int | None = None
    error_message: str | None = None
    kind: str = KIND_KILL_RESULT


@dataclass(frozen=True)
class CmdOutputReadRequestInput:
    target_seq: int
    stream: str
    offset: int
    nbytes: int
    ts_ms: int | None = None


@dataclass(frozen=True)
class CmdOutputReadRequest:
    target_seq: int
    stream: str
    offset: int
    nbytes: int
    ts_ms: int
    kind: str = KIND_OUTPUT_READ_REQUEST


@dataclass(frozen=True)
class CmdOutputReadResultInput:
    prev_seq: int
    status: str
    content: str
    bytes: int
    eof: bool
    content_encoding: str
    ts_ms: int | None = None
    target_seq: int | None = None
    stream: str | None = None
    offset: int | None = None
    nbytes: int | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class CmdOutputReadResult:
    prev_seq: int
    ts_ms: int
    status: str
    content: str
    bytes: int
    eof: bool
    content_encoding: str
    target_seq: int | None = None
    stream: str | None = None
    offset: int | None = None
    nbytes: int | None = None
    error_message: str | None = None
    kind: str = KIND_OUTPUT_READ_RESULT


CmdPayload = (
    CmdRunRequest
    | CmdRunAccepted
    | CmdRunResult
    | CmdKillRequest
    | CmdKillResult
    | CmdOutputReadRequest
    | CmdOutputReadResult
)


@dataclass(frozen=True)
class ParsedMessage:
    seq: int
    channel_id: int
    principal: int
    recipients: tuple[int, ...]
    payload: CmdPayload
