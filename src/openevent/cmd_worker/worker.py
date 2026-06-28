from __future__ import annotations

import base64
import inspect
import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue

from openevent.cmd_sdk import create_client, identify_kind, parse_message
from openevent.cmd_sdk.codec import dumps_payload, output_read_result_input_to_dict
from openevent.cmd_sdk.errors import CmdSDKError
from openevent.cmd_sdk.model import (
    KIND_KILL_REQUEST,
    KIND_KILL_RESULT,
    KIND_OUTPUT_READ_REQUEST,
    KIND_OUTPUT_READ_RESULT,
    KIND_RUN_ACCEPTED,
    KIND_RUN_REQUEST,
    KIND_RUN_RESULT,
    CmdKillRequest,
    CmdKillResultInput,
    CmdOutputReadRequest,
    CmdOutputReadResultInput,
    CmdRunAccepted,
    CmdRunRequest,
    CmdRunResult,
    CmdRunResultInput,
)

from .channel_resolver import ChannelResolver
from .config import CmdWorkerConfig
from .publisher import PublishFatal, ReliablePublisher
from .runner import CommandRunner, CommandStartError, RunningCommand, empty_output, now_ms

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunEvent:
    seq: int
    channel_id: int
    principal: int
    server_ts_ms: int
    request: CmdRunRequest


@dataclass(frozen=True)
class KillEvent:
    seq: int
    channel_id: int
    principal: int
    request: CmdKillRequest


@dataclass(frozen=True)
class OutputReadEvent:
    seq: int
    channel_id: int
    principal: int
    request: CmdOutputReadRequest


@dataclass(frozen=True)
class InvalidRequestEvent:
    seq: int
    channel_id: int
    principal: int
    kind: str
    error_message: str
    payload: bytes


@dataclass(frozen=True)
class CompletionEvent:
    channel_id: int
    seq: int
    return_code: int | None
    status_override: str | None


class CmdWorker:
    def __init__(self, config: CmdWorkerConfig, openevent_client):
        self.config = config
        self.openevent_client = openevent_client
        self.protocol_client = create_client(openevent_client)
        self.resolver = ChannelResolver(openevent_client, config.principal, config.token, config.channel_ids)
        self.runner = CommandRunner(config.output_dir)
        self.publisher = ReliablePublisher(
            self.protocol_client,
            openevent_client,
            config.principal,
            config.token,
            config.channel_ids,
            config.fetch_error_max_retries,
            config.fetch_error_retry_sleep_ms,
        )
        self.next_fetch_seq = 1
        self.pending: deque[RunEvent] = deque()
        self.running: dict[tuple[int, int], RunningCommand] = {}
        self.requests: dict[tuple[int, int], RunEvent] = {}
        self.accepted: dict[tuple[int, int], int | None] = {}
        self.results: dict[tuple[int, str, int], int | None] = {}
        self.completions: Queue[CompletionEvent] = Queue()
        self.stopping = False
        self.fetch_supports_channels = _fetch_supports_channels(openevent_client)

    def run(self) -> None:
        self.startup()
        while not self.stopping:
            completed = self._drain_completions()
            messages = self._fetch_round()
            for message in messages:
                self._observe_realtime_message(message)
            self._drain_completions()
            self._drain_pending()
            if not messages and not completed and not self.pending:
                time.sleep(self.config.fetch_idle_sleep_ms / 1000)

    def stop(self) -> None:
        self.stopping = True
        self._kill_all_running()

    def startup(self) -> None:
        self.resolver.validate_startup()
        self.runner.prepare_output_root()
        events = self._scan_history()
        for event in sorted(events, key=lambda item: item.seq):
            self._handle_event(event, historical=True)
        self._drain_pending()

    def _scan_history(self) -> list[RunEvent | KillEvent | OutputReadEvent | InvalidRequestEvent]:
        events: list[RunEvent | KillEvent | OutputReadEvent | InvalidRequestEvent] = []
        self.next_fetch_seq = 1
        while True:
            response = self._fetch_with_retries(self.next_fetch_seq)
            for message in response.messages:
                event = self._observe_message(message, record_results=True)
                if event is not None:
                    events.append(event)
            self.next_fetch_seq = int(response.next_seq)
            if self.next_fetch_seq > int(response.last_seq):
                break
        return events

    def _fetch_round(self):
        first = self._fetch_with_retries(self.next_fetch_seq)
        round_last_seq = int(first.last_seq)
        if int(first.next_seq) > round_last_seq and not first.messages:
            self.next_fetch_seq = int(first.next_seq)
            return []
        messages = [message for message in first.messages if int(message.seq) <= round_last_seq]
        self.next_fetch_seq = min(int(first.next_seq), round_last_seq + 1)
        while self.next_fetch_seq <= round_last_seq:
            response = self._fetch_with_retries(self.next_fetch_seq)
            messages.extend(message for message in response.messages if int(message.seq) <= round_last_seq)
            next_seq = min(int(response.next_seq), round_last_seq + 1)
            if next_seq <= self.next_fetch_seq:
                raise RuntimeError("Fetch did not advance next_seq")
            self.next_fetch_seq = next_seq
        return messages

    def _fetch_with_retries(self, from_seq: int):
        attempts = 0
        while True:
            try:
                kwargs = {"from_seq": from_seq, "limit": 1000, "only_my_recipient": False}
                if self.fetch_supports_channels:
                    kwargs["channels"] = self.config.channel_ids
                return self.openevent_client.fetch(self.config.principal, self.config.token, **kwargs)
            except Exception:
                attempts += 1
                if attempts > self.config.fetch_error_max_retries:
                    self._kill_all_running()
                    raise
                time.sleep(self.config.fetch_error_retry_sleep_ms / 1000)

    def _observe_realtime_message(self, message) -> None:
        event = self._observe_message(message, record_results=True)
        if event is not None:
            self._handle_event(event, historical=False)

    def _observe_message(self, message, record_results: bool):
        channel_id = int(message.channel_id)
        if not self.resolver.owns(channel_id):
            return None
        try:
            parsed = parse_message(message)
        except CmdSDKError as exc:
            return self._invalid_event_from_message(message, exc)
        payload = parsed.payload
        if isinstance(payload, CmdRunAccepted):
            key = (parsed.channel_id, payload.prev_seq)
            existing_seq = self.accepted.get(key)
            if record_results and existing_seq is not None and existing_seq != parsed.seq:
                raise RuntimeError(f"duplicate cmd.run.accepted for prev_seq={payload.prev_seq}")
            self.accepted[key] = parsed.seq
            return None
        if isinstance(payload, CmdRunResult):
            self._record_result(parsed.channel_id, KIND_RUN_RESULT, payload.prev_seq, parsed.seq)
            return None
        if payload.__class__.__name__ == "CmdKillResult":
            self._record_result(parsed.channel_id, KIND_KILL_RESULT, payload.prev_seq, parsed.seq)
            return None
        if payload.__class__.__name__ == "CmdOutputReadResult":
            self._record_result(parsed.channel_id, KIND_OUTPUT_READ_RESULT, payload.prev_seq, parsed.seq)
            return None
        if isinstance(payload, CmdRunRequest):
            return RunEvent(parsed.seq, parsed.channel_id, parsed.principal, int(message.ts_ms), payload)
        if isinstance(payload, CmdKillRequest):
            return KillEvent(parsed.seq, parsed.channel_id, parsed.principal, payload)
        if isinstance(payload, CmdOutputReadRequest):
            return OutputReadEvent(parsed.seq, parsed.channel_id, parsed.principal, payload)
        return None

    def _invalid_event_from_message(self, message, exc: CmdSDKError) -> InvalidRequestEvent | None:
        kind = identify_kind(message.payload)
        if kind in {KIND_RUN_REQUEST, KIND_KILL_REQUEST, KIND_OUTPUT_READ_REQUEST}:
            return InvalidRequestEvent(int(message.seq), int(message.channel_id), int(message.principal), kind, exc.message, message.payload)
        LOG.warning("invalid cmd payload ignored", extra={"seq": int(message.seq), "error": exc.code})
        return None

    def _handle_event(self, event, historical: bool) -> None:
        if isinstance(event, RunEvent):
            self._handle_run_event(event, historical)
        elif isinstance(event, KillEvent):
            if not self._has_result(event.channel_id, KIND_KILL_RESULT, event.seq):
                self._handle_kill_event(event)
        elif isinstance(event, OutputReadEvent):
            if not self._has_result(event.channel_id, KIND_OUTPUT_READ_RESULT, event.seq):
                self._handle_output_read_event(event)
        elif isinstance(event, InvalidRequestEvent):
            self._handle_invalid_request_event(event)

    def _handle_run_event(self, event: RunEvent, historical: bool) -> None:
        self.requests[(event.channel_id, event.seq)] = event
        if self._has_result(event.channel_id, KIND_RUN_RESULT, event.seq):
            return
        if historical and (event.channel_id, event.seq) in self.accepted:
            self._publish_recovery_failed(event)
            return
        self.pending.append(event)

    def _drain_pending(self) -> None:
        while self.pending and len(self.running) < self.config.max_concurrent_tasks:
            event = self.pending.popleft()
            if self._has_result(event.channel_id, KIND_RUN_RESULT, event.seq):
                continue
            self._start_or_timeout(event)

    def _start_or_timeout(self, event: RunEvent) -> None:
        timeout_ms = event.request.timeout_ms or self.config.default_timeout_ms
        deadline_ms = event.server_ts_ms + timeout_ms
        if now_ms() >= deadline_ms:
            self._publish_run_result(event, "TIMEOUT", timeout_ms=timeout_ms)
            return
        accepted = self.publisher.publish_run_accepted(event.channel_id, event.principal, event.seq, self.next_fetch_seq)
        self.accepted[(event.channel_id, event.seq)] = accepted.seq
        try:
            task = self.runner.start(event.channel_id, event.seq, event.request.command, event.request.workdir, timeout_ms, deadline_ms)
        except CommandStartError as exc:
            self._publish_run_result(event, "START_FAILED", timeout_ms=timeout_ms, error_message=str(exc))
            return
        self.running[(event.channel_id, event.seq)] = task
        thread = threading.Thread(target=self._wait_task, args=(task,), daemon=True)
        thread.start()

    def _wait_task(self, task: RunningCommand) -> None:
        remaining = max(0, (task.deadline_ms - now_ms()) / 1000)
        try:
            return_code = task.process.wait(timeout=remaining)
            self.completions.put(CompletionEvent(task.channel_id, task.seq, return_code, task.kill_reason))
        except Exception:
            task.kill_reason = task.kill_reason or "TIMEOUT"
            self.runner.kill_process_group(task)
            return_code = task.process.wait()
            self.completions.put(CompletionEvent(task.channel_id, task.seq, return_code, task.kill_reason))

    def _drain_completions(self) -> bool:
        handled = False
        while True:
            try:
                event = self.completions.get_nowait()
            except Empty:
                return handled
            handled = True
            task = self.running.pop((event.channel_id, event.seq), None)
            run_event = self.requests.get((event.channel_id, event.seq))
            if task is None or run_event is None or self._has_result(event.channel_id, KIND_RUN_RESULT, event.seq):
                continue
            if event.status_override == "TIMEOUT":
                self._publish_run_result(run_event, "TIMEOUT", timeout_ms=task.timeout_ms, started_at_ms=task.started_at_ms)
            elif event.status_override == "KILLED":
                self._publish_run_result(run_event, "KILLED", timeout_ms=task.timeout_ms, started_at_ms=task.started_at_ms)
            else:
                status = "SUCCESS" if event.return_code == 0 else "FAILED"
                self._publish_run_result(run_event, status, timeout_ms=task.timeout_ms, started_at_ms=task.started_at_ms, exit_code=event.return_code)

    def _handle_kill_event(self, event: KillEvent) -> None:
        target_key = (event.channel_id, event.request.target_seq)
        task = self.running.get(target_key)
        if task is not None:
            return_code = task.process.poll()
            if return_code is not None:
                task.process.wait()
                self.running.pop(target_key, None)
                run_event = self.requests.get(target_key)
                if run_event is not None:
                    self._publish_run_result(
                        run_event,
                        "SUCCESS" if return_code == 0 else "FAILED",
                        timeout_ms=task.timeout_ms,
                        started_at_ms=task.started_at_ms,
                        exit_code=return_code,
                    )
                self._publish_kill_result(event, "ALREADY_FINISHED")
                return
            task.kill_reason = "KILLED"
            self.runner.kill_process_group(task)
            self._publish_kill_result(event, "KILL_SENT")
            return
        pending = self._remove_pending(target_key)
        if pending is not None:
            self._publish_kill_result(event, "REMOVED_PENDING")
            self._publish_run_result(pending, "KILLED", timeout_ms=pending.request.timeout_ms or self.config.default_timeout_ms)
            return
        if self._has_result(event.channel_id, KIND_RUN_RESULT, event.request.target_seq):
            self._publish_kill_result(event, "ALREADY_FINISHED")
            return
        self._publish_kill_result(event, "NOT_FOUND")

    def _remove_pending(self, target_key: tuple[int, int]) -> RunEvent | None:
        found: RunEvent | None = None
        kept: deque[RunEvent] = deque()
        while self.pending:
            item = self.pending.popleft()
            if (item.channel_id, item.seq) == target_key and found is None:
                found = item
            else:
                kept.append(item)
        self.pending = kept
        return found

    def _handle_output_read_event(self, event: OutputReadEvent) -> None:
        result = self._build_output_read_result(event)
        published = self.publisher.publish_output_read_result(event.channel_id, event.principal, result, self.next_fetch_seq)
        self._record_result(event.channel_id, KIND_OUTPUT_READ_RESULT, event.seq, published.seq)

    def _build_output_read_result(self, event: OutputReadEvent) -> CmdOutputReadResultInput:
        req = event.request
        path = Path(self.config.output_dir) / str(event.channel_id) / f"{req.target_seq}.{req.stream}"
        base = {
            "prev_seq": event.seq,
            "target_seq": req.target_seq,
            "stream": req.stream,
            "offset": req.offset,
            "nbytes": req.nbytes,
        }
        if not path.exists():
            return CmdOutputReadResultInput(status="NOT_FOUND", bytes=0, eof=True, content_encoding="utf-8", content="", **base)
        try:
            size = path.stat().st_size
            if req.offset >= size:
                return CmdOutputReadResultInput(status="SUCCESS", bytes=0, eof=True, content_encoding="utf-8", content="", **base)
            max_read = min(req.nbytes, size - req.offset)
            read_size = max_read
            while True:
                with path.open("rb") as handle:
                    handle.seek(req.offset)
                    raw = handle.read(read_size)
                content_encoding, content = _encode_content(raw)
                result = CmdOutputReadResultInput(
                    status="SUCCESS",
                    bytes=len(raw),
                    eof=req.offset + len(raw) >= size,
                    content_encoding=content_encoding,
                    content=content,
                    **base,
                )
                if len(dumps_payload(output_read_result_input_to_dict(result))) <= self.config.open_event.max_payload_bytes or read_size == 0:
                    return result
                read_size = max(0, read_size // 2)
        except OSError as exc:
            return CmdOutputReadResultInput(status="READ_FAILED", bytes=0, eof=False, content_encoding="utf-8", content="", error_message=str(exc), **base)

    def _handle_invalid_request_event(self, event: InvalidRequestEvent) -> None:
        if event.kind == KIND_RUN_REQUEST and not self._has_result(event.channel_id, KIND_RUN_RESULT, event.seq):
            output_file = f"{event.channel_id}/{event.seq}"
            result = CmdRunResultInput(
                prev_seq=event.seq,
                status="REJECTED",
                finished_at_ms=now_ms(),
                stdout=empty_output(f"{output_file}.stdout"),
                stderr=empty_output(f"{output_file}.stderr"),
                error_message=event.error_message,
            )
            published = self.publisher.publish_run_result(event.channel_id, event.principal, result, self.next_fetch_seq)
            self._record_result(event.channel_id, KIND_RUN_RESULT, event.seq, published.seq)
        elif event.kind == KIND_KILL_REQUEST and not self._has_result(event.channel_id, KIND_KILL_RESULT, event.seq):
            target_seq = _best_effort_int(event.payload, "target_seq")
            result = CmdKillResultInput(prev_seq=event.seq, target_seq=target_seq, status="REJECTED", error_message=event.error_message)
            published = self.publisher.publish_kill_result(event.channel_id, event.principal, result, self.next_fetch_seq)
            self._record_result(event.channel_id, KIND_KILL_RESULT, event.seq, published.seq)
        elif event.kind == KIND_OUTPUT_READ_REQUEST and not self._has_result(event.channel_id, KIND_OUTPUT_READ_RESULT, event.seq):
            data = _best_effort_dict(event.payload)
            result = CmdOutputReadResultInput(
                prev_seq=event.seq,
                status="REJECTED",
                target_seq=_best_effort_positive_int_from_dict(data, "target_seq"),
                stream=data.get("stream") if data.get("stream") in {"stdout", "stderr"} else None,
                offset=_best_effort_non_negative_int_from_dict(data, "offset"),
                nbytes=_best_effort_positive_int_from_dict(data, "nbytes"),
                bytes=0,
                eof=True,
                content_encoding="utf-8",
                content="",
                error_message=event.error_message,
            )
            published = self.publisher.publish_output_read_result(event.channel_id, event.principal, result, self.next_fetch_seq)
            self._record_result(event.channel_id, KIND_OUTPUT_READ_RESULT, event.seq, published.seq)

    def _publish_recovery_failed(self, event: RunEvent) -> None:
        timeout_ms = event.request.timeout_ms or self.config.default_timeout_ms
        result = self._run_result_input(event, "RECOVERY_FAILED", timeout_ms=timeout_ms, error_message="worker recovered accepted run request without final result")
        published = self.publisher.publish_run_result(event.channel_id, event.principal, result, self.next_fetch_seq)
        self._record_result(event.channel_id, KIND_RUN_RESULT, event.seq, published.seq)

    def _publish_run_result(
        self,
        event: RunEvent,
        status: str,
        timeout_ms: int | None = None,
        started_at_ms: int | None = None,
        exit_code: int | None = None,
        error_message: str | None = None,
    ) -> None:
        result = self._run_result_input(event, status, timeout_ms=timeout_ms, started_at_ms=started_at_ms, exit_code=exit_code, error_message=error_message)
        published = self.publisher.publish_run_result(event.channel_id, event.principal, result, self.next_fetch_seq)
        self._record_result(event.channel_id, KIND_RUN_RESULT, event.seq, published.seq)

    def _run_result_input(
        self,
        event: RunEvent,
        status: str,
        timeout_ms: int | None = None,
        started_at_ms: int | None = None,
        exit_code: int | None = None,
        error_message: str | None = None,
    ) -> CmdRunResultInput:
        return CmdRunResultInput(
            prev_seq=event.seq,
            status=status,
            timeout_ms=timeout_ms,
            started_at_ms=started_at_ms,
            finished_at_ms=now_ms(),
            exit_code=exit_code,
            stdout=self.runner.output_for(event.channel_id, event.seq, "stdout"),
            stderr=self.runner.output_for(event.channel_id, event.seq, "stderr"),
            error_message=error_message,
        )

    def _publish_kill_result(self, event: KillEvent, status: str, error_message: str | None = None) -> None:
        result = CmdKillResultInput(prev_seq=event.seq, target_seq=event.request.target_seq, status=status, error_message=error_message)
        published = self.publisher.publish_kill_result(event.channel_id, event.principal, result, self.next_fetch_seq)
        self._record_result(event.channel_id, KIND_KILL_RESULT, event.seq, published.seq)

    def _record_result(self, channel_id: int, kind: str, prev_seq: int, seq: int | None) -> None:
        key = (channel_id, kind, prev_seq)
        existing_seq = self.results.get(key)
        if existing_seq is not None and seq is not None and existing_seq != seq:
            raise RuntimeError(f"duplicate {kind} for prev_seq={prev_seq}")
        if existing_seq is None:
            self.results[key] = seq

    def _has_result(self, channel_id: int, kind: str, prev_seq: int) -> bool:
        return (channel_id, kind, prev_seq) in self.results

    def _kill_all_running(self) -> None:
        for task in list(self.running.values()):
            task.kill_reason = task.kill_reason or "KILLED"
            self.runner.kill_process_group(task)


def _encode_content(raw: bytes) -> tuple[str, str]:
    try:
        return "utf-8", raw.decode("utf-8")
    except UnicodeDecodeError:
        return "base64", base64.b64encode(raw).decode("ascii")


def _best_effort_dict(payload: bytes) -> dict:
    try:
        data = json.loads(payload.decode("utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _best_effort_int(payload: bytes, key: str) -> int | None:
    data = _best_effort_dict(payload)
    return _best_effort_positive_int_from_dict(data, key)


def _best_effort_positive_int_from_dict(data: dict, key: str) -> int | None:
    value = data.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _best_effort_non_negative_int_from_dict(data: dict, key: str) -> int | None:
    value = data.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _fetch_supports_channels(openevent_client) -> bool:
    try:
        return "channels" in inspect.signature(openevent_client.fetch).parameters
    except (AttributeError, TypeError, ValueError):
        return False
