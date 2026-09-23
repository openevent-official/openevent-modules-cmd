from __future__ import annotations

import os
import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openevent.cmd_worker.runner import CommandRunner, CommandStartError, now_ms


class RunnerTest(unittest.TestCase):
    def test_command_writes_stdout_and_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = CommandRunner(tmp)
            runner.prepare_output_root()
            task = runner.start(2001, 123, "printf hello; printf err >&2", None, 10000, now_ms() + 10000)
            self.assertEqual(runner.wait(task, timeout=5), 0)
            stdout = runner.output_for(2001, 123, "stdout")
            stderr = runner.output_for(2001, 123, "stderr")
            self.assertEqual(stdout.content, "hello")
            self.assertEqual(stderr.content, "err")

    def test_long_output_is_file_metadata_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = CommandRunner(tmp)
            runner.prepare_output_root()
            path = Path(tmp) / "2001"
            path.mkdir()
            (path / "123.stdout").write_bytes(b"x" * 2048)
            output = runner.output_for(2001, 123, "stdout")
            self.assertEqual(output.bytes, 2048)
            self.assertIsNone(output.content)

    def test_short_output_growth_does_not_expand_the_read(self) -> None:
        output, read_sizes = self._output_with_concurrent_change(b"x" * 1024, "ab", b"y" * 4096)
        self.assertEqual(read_sizes, [1024])
        self.assertEqual(output.bytes, 1024)
        self.assertEqual(output.content, "x" * 1024)

    def test_short_output_shrink_reports_the_bytes_actually_read(self) -> None:
        output, read_sizes = self._output_with_concurrent_change(b"original output", "wb", b"new")
        self.assertLessEqual(read_sizes[0], 1024)
        self.assertEqual(output.bytes, 3)
        self.assertEqual(output.content, "new")

    def _output_with_concurrent_change(self, initial, mode, changed):
        with tempfile.TemporaryDirectory() as tmp:
            runner = CommandRunner(tmp)
            channel_dir = Path(tmp) / "2001"
            channel_dir.mkdir()
            path = channel_dir / "123.stdout"
            path.write_bytes(initial)
            original_open = Path.open
            read_sizes = []

            class ChangingFile:
                def __init__(self, handle):
                    self.handle = handle

                def __enter__(self):
                    self.handle.__enter__()
                    return self

                def __exit__(self, *args):
                    return self.handle.__exit__(*args)

                def fileno(self):
                    return self.handle.fileno()

                def read(self, size=-1):
                    read_sizes.append(size)
                    with original_open(path, mode) as writer:
                        writer.write(changed)
                    return self.handle.read(size)

            def open_changing_file(file_path, *args, **kwargs):
                return ChangingFile(original_open(file_path, *args, **kwargs))

            with patch.object(Path, "open", open_changing_file):
                output = runner.output_for(2001, 123, "stdout")
            return output, read_sizes

    def test_channel_output_directory_failure_is_start_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = CommandRunner(tmp)
            runner.prepare_output_root()
            (Path(tmp) / "2001").write_text("not a directory", encoding="utf-8")
            with self.assertRaises(CommandStartError):
                runner.start(2001, 123, "true", None, 10000, now_ms() + 10000)

    def test_kill_process_group_stops_command_and_reports_signal_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = CommandRunner(tmp)
            runner.prepare_output_root()
            task = runner.start(2001, 123, "exec sleep 30", None, 10000, now_ms() + 10000)
            try:
                self.assertEqual(os.getpgid(task.process.pid), task.process.pid)
                self.assertTrue(runner.terminate(task, "KILLED"))
                self.assertEqual(runner.wait(task, timeout=5), -signal.SIGKILL)
                self.assertFalse(runner.terminate(task, "KILLED"))
            finally:
                runner.terminate(task, "KILLED")
                runner.wait(task, timeout=5)


if __name__ == "__main__":
    unittest.main()
