from __future__ import annotations

import base64
import os
import signal
import subprocess
import time
from dataclasses import dataclass
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
    stdout_path: Path
    stderr_path: Path
    started_at_ms: int
    timeout_ms: int
    deadline_ms: int
    kill_reason: str | None = None


class CommandRunner:
    def __init__(self, output_dir: str | Path):
        self.output_dir = Path(output_dir)

    def prepare_output_root(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        probe = self.output_dir / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()

    def start(self, channel_id: int, seq: int, command: str, workdir: str | None, timeout_ms: int, deadline_ms: int) -> RunningCommand:
        cwd: str | None = None
        if workdir is not None:
            path = Path(workdir)
            if not path.is_dir():
                raise CommandStartError("workdir does not exist or is not a directory")
            cwd = str(path)
        channel_dir = self.output_dir / str(channel_id)
        try:
            channel_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CommandStartError(f"cannot create channel output directory: {exc}") from exc
        stdout_path = channel_dir / f"{seq}.stdout"
        stderr_path = channel_dir / f"{seq}.stderr"
        stdout_file = None
        stderr_file = None
        try:
            stdout_file = stdout_path.open("wb")
            stderr_file = stderr_path.open("wb")
            process = subprocess.Popen(
                command,
                shell=True,
                cwd=cwd,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
                close_fds=True,
            )
        except OSError as exc:
            raise CommandStartError(f"cannot open output files or start shell process: {exc}") from exc
        finally:
            if stdout_file is not None:
                stdout_file.close()
            if stderr_file is not None:
                stderr_file.close()
        return RunningCommand(
            channel_id=channel_id,
            seq=seq,
            process=process,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            started_at_ms=now_ms(),
            timeout_ms=timeout_ms,
            deadline_ms=deadline_ms,
        )

    def kill_process_group(self, task: RunningCommand) -> None:
        if task.process.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(task.process.pid), signal.SIGKILL)
        except ProcessLookupError:
            return
        except OSError:
            task.process.kill()

    def output_for(self, channel_id: int, seq: int, stream: str) -> CmdOutput:
        suffix = "stdout" if stream == "stdout" else "stderr"
        path = self.output_dir / str(channel_id) / f"{seq}.{suffix}"
        return output_object(path, channel_id, seq, stream)


def now_ms() -> int:
    return int(time.time() * 1000)


def output_object(path: Path, channel_id: int, seq: int, stream: str) -> CmdOutput:
    rel_file = f"{channel_id}/{seq}.{stream}"
    if not path.exists():
        return empty_output(rel_file)
    try:
        size = path.stat().st_size
    except OSError:
        return empty_output(rel_file)
    if size > INLINE_OUTPUT_BYTES:
        return CmdOutput(file=rel_file, bytes=size)
    try:
        data = path.read_bytes()
    except OSError:
        return empty_output(rel_file)
    try:
        content = data.decode("utf-8")
        return CmdOutput(file=rel_file, bytes=size, content_encoding="utf-8", content=content)
    except UnicodeDecodeError:
        return CmdOutput(file=rel_file, bytes=size, content_encoding="base64", content=base64.b64encode(data).decode("ascii"))


def empty_output(file: str) -> CmdOutput:
    return CmdOutput(file=file, bytes=0, content_encoding="utf-8", content="")
