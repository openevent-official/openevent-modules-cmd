from __future__ import annotations

import base64
import json
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from openevent.cmd_sdk.model import CmdOutput

INLINE_OUTPUT_BYTES = 1024


class CommandStartError(RuntimeError):
    pass


@dataclass
class RunningCommand:
    channel_id: int
    seq: int
    process: subprocess.Popen
    started_at_ms: int
    timeout_ms: int
    deadline_ms: int
    kill_reason: str | None = None
    termination_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    return_code: int | None = field(default=None, init=False)


class CommandRunner:
    def __init__(self, output_dir: str | Path):
        self.output_dir = Path(output_dir)

    def prepare_output_root(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        probe = self.output_dir / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()

    def start(self, channel_id: int, seq: int, command: str, workdir: str | None, timeout_ms: int, deadline_ms: int, *, object_keys=()) -> RunningCommand:
        channel_dir = self.output_dir / str(channel_id)
        try:
            channel_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CommandStartError(f"cannot create channel output directory: {exc}") from exc
        stdout_path = channel_dir / f"{seq}.stdout"
        stderr_path = channel_dir / f"{seq}.stderr"
        environment = os.environ.copy()
        environment["OPENEVENT_OBJECT_KEYS"] = json.dumps(
            [{"object_id": int(key.object_id), "object_token": key.object_token} for key in object_keys],
            ensure_ascii=True, separators=(",", ":"),
        )
        process = None
        try:
            with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
                process = subprocess.Popen(
                    command,
                    shell=True,
                    cwd=workdir,
                    env=environment,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    start_new_session=True,
                    close_fds=True,
                )
            return RunningCommand(
                channel_id=channel_id,
                seq=seq,
                process=process,
                started_at_ms=now_ms(),
                timeout_ms=timeout_ms,
                deadline_ms=deadline_ms,
            )
        except BaseException as exc:
            if process is not None:
                # Until handoff, only start() owns this unreaped child. Signal
                # its group before wait() can release the leader's PID/PGID.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            if isinstance(exc, (OSError, ValueError)):
                raise CommandStartError(f"cannot prepare output files or start shell process: {exc}") from exc
            raise

    def poll(self, task: RunningCommand) -> int | None:
        with task.termination_lock:
            return self._poll_locked(task)

    def wait(self, task: RunningCommand, timeout: float | None = None) -> int:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            return_code = self.poll(task)
            if return_code is not None:
                return return_code
            delay = 0.05
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(task.process.args, timeout)
                delay = min(delay, remaining)
            time.sleep(delay)

    def terminate(self, task: RunningCommand, reason: str) -> bool:
        with task.termination_lock:
            if self._poll_locked(task) is not None:
                return False
            try:
                os.killpg(task.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return False
            task.kill_reason = task.kill_reason or reason
            return True

    def _poll_locked(self, task: RunningCommand) -> int | None:
        if task.return_code is None:
            status = os.waitid(os.P_PID, task.process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if status is None:
                return None
            # Keep the exited leader waitable until its group has been signaled,
            # so the PID/PGID cannot be recycled before this final cleanup.
            try:
                os.killpg(task.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            task.return_code = status.si_status if status.si_code == os.CLD_EXITED else -status.si_status
        # Cache before reaping: an interrupt during wait() must never cause a
        # later call to signal the old numeric PGID again.
        if task.process.returncode is None:
            task.process.wait()
        # wait() can lose the exit status if interrupted after waitpid reaped
        # the child; the WNOWAIT observation remains the authoritative value.
        task.process.returncode = task.return_code
        return task.return_code

    def output_for(self, channel_id: int, seq: int, stream: str) -> CmdOutput:
        suffix = "stdout" if stream == "stdout" else "stderr"
        path = self.output_dir / str(channel_id) / f"{seq}.{suffix}"
        return output_object(path, channel_id, seq, stream)


def now_ms() -> int:
    return int(time.time() * 1000)


def output_object(path: Path, channel_id: int, seq: int, stream: str) -> CmdOutput:
    rel_file = f"{channel_id}/{seq}.{stream}"
    try:
        with path.open("rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            if size > INLINE_OUTPUT_BYTES:
                return CmdOutput(file=rel_file, bytes=size)
            data = handle.read(min(size, INLINE_OUTPUT_BYTES))
    except FileNotFoundError:
        return empty_output(rel_file)
    size = len(data)
    try:
        content = data.decode("utf-8")
        return CmdOutput(file=rel_file, bytes=size, content_encoding="utf-8", content=content)
    except UnicodeDecodeError:
        return CmdOutput(file=rel_file, bytes=size, content_encoding="base64", content=base64.b64encode(data).decode("ascii"))


def empty_output(file: str) -> CmdOutput:
    return CmdOutput(file=file, bytes=0, content_encoding="utf-8", content="")
