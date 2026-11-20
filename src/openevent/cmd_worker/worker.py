from __future__ import annotations

import base64
import os
import subprocess
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from queue import Empty, Queue
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openevent.sdk.proto import openevent_pb2

import grpc

from openevent.cmd_sdk import parse_message
from openevent.cmd_sdk.codec import json_bytes, output_read_result_input_to_dict
from openevent.cmd_sdk.errors import CmdSDKError
from openevent.cmd_sdk.model import (
    CmdKillRequest,
    CmdKillResult,
    CmdKillResultInput,
    CmdOutputReadRequest,
    CmdOutputReadResult,
    CmdOutputReadResultInput,
    CmdRunAccepted,
    CmdRunRequest,
    CmdRunResult,
    CmdRunResultInput,
)

from .channel_resolver import validate_channels
from .config import CmdWorkerConfig
from .publisher import ReliablePublisher
from .runner import CommandRunner, CommandStartError, RunningCommand, empty_output, now_ms


@dataclass(frozen=True)
class RunEvent:
    seq: int
    channel_id: int
    principal: int
    timeout_ms: int
    deadline_ms: int
    request: CmdRunRequest
    object_keys: tuple[openevent_pb2.ObjectKey, ...] = ()


@dataclass(frozen=True)
class ActiveRun:
    principal: int
    command: RunningCommand


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
class CompletionEvent:
    channel_id: int
    seq: int
    return_code: int | None
    error: Exception | None = None


@dataclass
class _RecoveryState:
    requests: dict[
        tuple[int, int], RunEvent | KillEvent | OutputReadEvent
    ] = field(default_factory=dict)
    accepted: set[tuple[int, int]] = field(default_factory=set)


class CmdWorker:
    def __init__(self, config: CmdWorkerConfig, openevent_client):
        self.config = config
        self.openevent_client = openevent_client
        self.runner = CommandRunner(config.output_dir)
        self.publisher = ReliablePublisher(
            openevent_client,
            config.principal,
            config.token,
            config.open_event.max_payload_bytes,
        )
        self.next_fetch_seq = 1
        self.pending: OrderedDict[tuple[int, int], RunEvent] = OrderedDict()
        self._next_pending_deadline_ms: int | None = None
        self.running: dict[tuple[int, int], ActiveRun] = {}
        self.completions: Queue[CompletionEvent] = Queue()
        self.stopping = False
        self.starting_command = False

    def run(self) -> None:
        try:
            self.startup()
            while not self.stopping:
                completed = self._drain_completions()
                observed, page_completed = self._fetch_round()
                completed = page_completed or completed
                scheduled = self._drain_pending()
                if not self.stopping and not observed and not completed and not scheduled:
                    delay_ms = self.config.fetch_idle_sleep_ms
                    if self._next_pending_deadline_ms is not None:
                        delay_ms = min(delay_ms, max(0, self._next_pending_deadline_ms - now_ms()))
                    try:
                        event = self.completions.get(timeout=delay_ms / 1000)
                    except Empty:
                        continue
                    self._handle_completion(event)
        finally:
            self.stop()

    def stop(self) -> None:
        self.stopping = True
        self._kill_all_running()

    def startup(self) -> None:
        validate_channels(self.openevent_client, self.config)
        self.runner.prepare_output_root()
        recovery = self._scan_history()
        for event in recovery.requests.values():
            if isinstance(event, RunEvent) and (event.channel_id, event.seq) in recovery.accepted:
                self._publish_recovery_failed(event)
            else:
                self._handle_event(event)
        del recovery
        self._drain_pending()

    def _scan_history(self) -> _RecoveryState:
        recovery = _RecoveryState()
        self.next_fetch_seq = 1

        def observe(message: openevent_pb2.EventMessage) -> None:
            event = self._observe_message(message, recovery=recovery)
            if event is not None:
                recovery.requests[(event.channel_id, event.seq)] = event

        self._consume_pages(observe)
        return recovery

    def _fetch_round(self) -> tuple[bool, bool]:
        """Consume pages through one fixed boundary before scheduling new runs."""
        completed = self._expire_pending()

        def after_page() -> None:
            nonlocal completed
            completed = self._drain_completions() or completed
            completed = self._expire_pending() or completed

        observed = self._consume_pages(self._observe_realtime_message, after_page=after_page)
        return observed, completed

    def _consume_pages(
        self,
        on_message: Callable[[openevent_pb2.EventMessage], None],
        *,
        after_page: Callable[[], None] | None = None,
    ) -> bool:
        """Consume through the first page's tail, releasing each page before proceeding."""
        observed = False
        response = self._fetch_with_retries(self.next_fetch_seq)
        last_seq = int(response.last_seq)
        while True:
            for message in response.messages:
                if int(message.seq) <= last_seq:
                    on_message(message)
                    observed = True
            next_seq = min(int(response.next_seq), last_seq + 1)
            if next_seq <= self.next_fetch_seq and self.next_fetch_seq <= last_seq:
                raise RuntimeError("Fetch did not advance next_seq")
            self.next_fetch_seq = next_seq
            # Release this page, including the loop's last message, before
            # invoking the page callback or fetching another potentially large page.
            message = None
            del response
            if after_page is not None:
                after_page()
            if self.next_fetch_seq > last_seq:
                return observed
            response = self._fetch_with_retries(self.next_fetch_seq)

    def _fetch_with_retries(self, from_seq: int):
        attempts = 0
        while True:
            try:
                kwargs = {
                    "from_seq": from_seq,
                    "limit": 1000,
                    "only_my_recipient": False,
                    "channels": self.config.channel_ids,
                }
                return self.openevent_client.fetch(self.config.principal, self.config.token, **kwargs)
            except grpc.RpcError:
                attempts += 1
                if attempts > self.config.fetch_error_max_retries:
                    raise
                time.sleep(self.config.fetch_error_retry_sleep_ms / 1000)

    def _observe_realtime_message(self, message) -> None:
        event = self._observe_message(message)
        if event is not None:
            self._handle_event(event)

    def _observe_message(self, message, *, recovery: _RecoveryState | None = None):
        # Ordinary cmd channels also contain OpenEvent system messages.
        if int(message.principal) == 0:
            return None
        channel_id = int(message.channel_id)
        try:
            parsed = parse_message(message)
        except CmdSDKError as exc:
            raise RuntimeError(
                f"failed to parse Cmd message channel_id={channel_id} seq={int(message.seq)}: {exc.code}"
            ) from exc
        payload = parsed.payload
        if isinstance(payload, CmdRunAccepted):
            if recovery is not None and (channel_id, payload.prev_seq) in recovery.requests:
                recovery.accepted.add((channel_id, payload.prev_seq))
            return None
        if isinstance(payload, (CmdRunResult, CmdKillResult, CmdOutputReadResult)):
            if recovery is not None:
                # Close just this request, including ones from earlier pages;
                # never rescan the accumulated history to release its payload.
                recovery.requests.pop((channel_id, payload.prev_seq), None)
                if isinstance(payload, CmdRunResult):
                    recovery.accepted.discard((channel_id, payload.prev_seq))
            return None
        if isinstance(payload, CmdRunRequest):
            timeout_ms = payload.timeout_ms or self.config.default_timeout_ms
            return RunEvent(
                seq=parsed.seq,
                channel_id=parsed.channel_id,
                principal=parsed.principal,
                timeout_ms=timeout_ms,
                deadline_ms=int(message.ts_ms) + timeout_ms,
                request=payload,
                object_keys=parsed.object_keys,
            )
        if isinstance(payload, CmdKillRequest):
            return KillEvent(parsed.seq, parsed.channel_id, parsed.principal, payload)
        if isinstance(payload, CmdOutputReadRequest):
            return OutputReadEvent(parsed.seq, parsed.channel_id, parsed.principal, payload)
        return None

    def _handle_event(self, event) -> None:
        if isinstance(event, RunEvent):
            self._handle_run_event(event)
        elif isinstance(event, KillEvent):
            self._handle_kill_event(event)
        elif isinstance(event, OutputReadEvent):
            self._handle_output_read_event(event)

    def _handle_run_event(self, event: RunEvent) -> None:
        self.pending[(event.channel_id, event.seq)] = event
        if self._next_pending_deadline_ms is None or event.deadline_ms < self._next_pending_deadline_ms:
            self._next_pending_deadline_ms = event.deadline_ms

    def _expire_pending(self) -> bool:
        if not self.pending:
            self._next_pending_deadline_ms = None
            return False
        current_ms = now_ms()
        if self._next_pending_deadline_ms is not None and current_ms < self._next_pending_deadline_ms:
            return False

        # Removing the earliest job can leave an early wake-up. Recompute the
        # minimum only when it is reached, without retaining IDs of removed jobs.
        next_deadline = None
        expired = []
        for key, event in self.pending.items():
            if event.deadline_ms <= current_ms:
                expired.append(key)
            elif next_deadline is None or event.deadline_ms < next_deadline:
                next_deadline = event.deadline_ms
        self._next_pending_deadline_ms = next_deadline
        for key in expired:
            event = self.pending.pop(key)
            self._publish_run_result(event, "TIMEOUT")
        return bool(expired)

    def _drain_pending(self) -> bool:
        handled = self._expire_pending()
        while not self.stopping and self.pending and len(self.running) < self.config.max_concurrent_tasks:
            _, event = self.pending.popitem(last=False)
            handled = True
            self._start_or_timeout(event)
        return handled

    def _start_or_timeout(self, event: RunEvent) -> None:
        if now_ms() >= event.deadline_ms:
            self._publish_run_result(event, "TIMEOUT")
            return
        self.publisher.publish_run_accepted(event.channel_id, event.principal, event.seq)
        if now_ms() >= event.deadline_ms:
            self._publish_run_result(event, "TIMEOUT")
            return
        start_error = None
        # CLI stop handlers defer raising while a new process is being created
        # and registered, so run()'s finally can always find it for cleanup.
        self.starting_command = True
        try:
            if self.stopping:
                return
            try:
                task = self.runner.start(
                    event.channel_id, event.seq, event.request.command, event.request.workdir,
                    event.timeout_ms, event.deadline_ms, object_keys=event.object_keys,
                )
            except CommandStartError as exc:
                start_error = exc
            else:
                self.running[(event.channel_id, event.seq)] = ActiveRun(event.principal, task)
        finally:
            self.starting_command = False
        if self.stopping:
            return
        if start_error is not None:
            self._publish_run_result(event, "START_FAILED", error_message=str(start_error))
            return
        thread = threading.Thread(target=self._wait_task, args=(task,), daemon=True)
        thread.start()

    def _wait_task(self, task: RunningCommand) -> None:
        try:
            remaining = max(0, (task.deadline_ms - now_ms()) / 1000)
            try:
                return_code = self.runner.wait(task, timeout=remaining)
            except subprocess.TimeoutExpired:
                self.runner.terminate(task, "TIMEOUT")
                return_code = self.runner.wait(task)
        except Exception as exc:
            event = CompletionEvent(task.channel_id, task.seq, None, error=exc)
        else:
            event = CompletionEvent(task.channel_id, task.seq, return_code)
        self.completions.put(event)

    def _drain_completions(self) -> bool:
        handled = False
        while True:
            try:
                event = self.completions.get_nowait()
            except Empty:
                return handled
            handled = True
            self._handle_completion(event)

    def _handle_completion(self, event: CompletionEvent) -> None:
        if event.error is not None:
            raise RuntimeError(
                f"failed to wait for command channel_id={event.channel_id} seq={event.seq}"
            ) from event.error
        active = self.running.get((event.channel_id, event.seq))
        if active is not None:
            self._finish_task(active, event.return_code)

    def _finish_task(self, active: ActiveRun, return_code: int) -> None:
        task = active.command
        key = (task.channel_id, task.seq)
        with task.termination_lock:
            reason = task.kill_reason
        status = reason or ("SUCCESS" if return_code == 0 else "FAILED")
        self._publish_run_result(
            active, status,
            started_at_ms=task.started_at_ms,
            exit_code=return_code if reason is None else None,
        )
        self.running.pop(key, None)

    def _handle_kill_event(self, event: KillEvent) -> None:
        target_key = (event.channel_id, event.request.target_seq)
        active = self.running.get(target_key)
        if active is not None:
            task = active.command
            if self.runner.terminate(task, "KILLED"):
                self._publish_kill_result(event, "KILL_SENT")
            else:
                self._finish_task(active, self.runner.wait(task))
                self._publish_kill_result(event, "NOT_RUNNING")
            return
        pending = self.pending.pop(target_key, None)
        if pending is not None:
            if now_ms() >= pending.deadline_ms:
                self._publish_run_result(pending, "TIMEOUT")
            else:
                self._publish_run_result(pending, "KILLED")
                self._publish_kill_result(event, "REMOVED_PENDING")
                return
        self._publish_kill_result(event, "NOT_RUNNING")

    def _handle_output_read_event(self, event: OutputReadEvent) -> None:
        result = self._build_output_read_result(event)
        self.publisher.publish_output_read_result(event.channel_id, event.principal, result)

    def _build_output_read_result(self, event: OutputReadEvent) -> CmdOutputReadResultInput:
        req = event.request
        path = Path(self.config.output_dir) / str(event.channel_id) / f"{req.target_seq}.{req.stream}"
        base = {
            "prev_seq": event.seq,
            "target_seq": req.target_seq,
            "stream": req.stream,
            "offset": req.offset,
            "nbytes": req.nbytes,
            "ts_ms": now_ms(),
        }

        def failed(reason: str) -> CmdOutputReadResultInput:
            return CmdOutputReadResultInput(
                status="READ_FAILED", eof=False, content_encoding="utf-8",
                content="", error_message=reason, **base,
            )

        try:
            with path.open("rb") as handle:
                size = os.fstat(handle.fileno()).st_size
                if req.offset + req.nbytes > size:
                    return failed("requested range exceeds output file size")
                # Neither JSON nor base64 can make these raw bytes smaller.
                if req.nbytes > self.config.open_event.max_payload_bytes:
                    return failed("requested range exceeds max_payload_bytes")
                handle.seek(req.offset)
                raw = handle.read(req.nbytes)
        except FileNotFoundError:
            return CmdOutputReadResultInput(status="NOT_FOUND", eof=True, content_encoding="utf-8", content="", **base)
        except OSError as exc:
            return failed(str(exc))
        if len(raw) != req.nbytes:
            return failed("output file did not provide the complete requested range")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        result = CmdOutputReadResultInput(
            status="SUCCESS", eof=req.offset + req.nbytes == size,
            content_encoding="base64" if text is None else "utf-8", content="", **base,
        )
        # An empty JSON string already includes the quotes. Only its contents
        # need to fit in the space left by the other response fields.
        overhead = len(json_bytes(output_read_result_input_to_dict(result)))
        available = self.config.open_event.max_payload_bytes - overhead
        content = _fit_output_content(raw, text, available)
        if content is None:
            return failed("encoded output read result exceeds max_payload_bytes")
        return replace(result, content=content)

    def _publish_recovery_failed(self, event: RunEvent) -> None:
        self._publish_run_result(
            event, "RECOVERY_FAILED",
            error_message="worker recovered accepted run request without final result",
        )

    def _publish_run_result(
        self,
        run: RunEvent | ActiveRun,
        status: str,
        started_at_ms: int | None = None,
        exit_code: int | None = None,
        error_message: str | None = None,
    ) -> None:
        task = run.command if isinstance(run, ActiveRun) else run
        output_file = f"{task.channel_id}/{task.seq}"
        empty = started_at_ms is None and status != "RECOVERY_FAILED"
        result = CmdRunResultInput(
            prev_seq=task.seq,
            status=status,
            timeout_ms=task.timeout_ms,
            started_at_ms=started_at_ms,
            exit_code=exit_code,
            stdout=empty_output(f"{output_file}.stdout") if empty else self.runner.output_for(task.channel_id, task.seq, "stdout"),
            stderr=empty_output(f"{output_file}.stderr") if empty else self.runner.output_for(task.channel_id, task.seq, "stderr"),
            error_message=error_message,
        )
        self.publisher.publish_run_result(task.channel_id, run.principal, result)

    def _publish_kill_result(self, event: KillEvent, status: str) -> None:
        result = CmdKillResultInput(prev_seq=event.seq, target_seq=event.request.target_seq, status=status)
        self.publisher.publish_kill_result(event.channel_id, event.principal, result)

    def _kill_all_running(self) -> None:
        tasks = [active.command for active in self.running.values()]
        first_error: BaseException | None = None
        for task in tasks:
            try:
                self.runner.terminate(task, "KILLED")
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
        for task in tasks:
            try:
                self.runner.wait(task)
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error


def _fit_output_content(raw: bytes, text: str | None, available: int) -> str | None:
    if text is None:
        if 4 * ((len(raw) + 2) // 3) > available:
            return None
        return base64.b64encode(raw).decode("ascii")
    if len(raw) > available:
        return None
    # A control byte needs at most six JSON bytes. Only ambiguous sizes need
    # to count actual escaping, using the same encoder as the final publish.
    if 6 * len(raw) > available:
        chunk_chars = min(16 * 1024, max(1, available // 6))
        for start in range(0, len(text), chunk_chars):
            available -= len(json_bytes(text[start:start + chunk_chars])) - 2
            if available < 0:
                return None
    return text
