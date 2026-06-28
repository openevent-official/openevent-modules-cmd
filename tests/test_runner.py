from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openevent.cmd_worker.runner import CommandRunner, CommandStartError, now_ms


class RunnerTest(unittest.TestCase):
    def test_command_writes_stdout_and_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = CommandRunner(tmp)
            runner.prepare_output_root()
            task = runner.start(2001, 123, "printf hello; printf err >&2", None, 10000, now_ms() + 10000)
            self.assertEqual(task.process.wait(timeout=5), 0)
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

    def test_channel_output_directory_failure_is_start_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = CommandRunner(tmp)
            runner.prepare_output_root()
            (Path(tmp) / "2001").write_text("not a directory", encoding="utf-8")
            with self.assertRaises(CommandStartError):
                runner.start(2001, 123, "true", None, 10000, now_ms() + 10000)


if __name__ == "__main__":
    unittest.main()
