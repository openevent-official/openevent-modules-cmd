import os

from openevent.cmd_worker.runner import CommandRunner, now_ms


def test_command_writes_stdout_and_stderr(tmp_path):
    runner = CommandRunner(tmp_path)
    runner.prepare_output_root()
    task = runner.start(2001, 123, "printf hello; printf err >&2", None, 10000, now_ms() + 10000)
    assert runner.wait(task, timeout=5) == 0
    assert runner.output_for(2001, 123, "stdout").content == "hello"
    assert runner.output_for(2001, 123, "stderr").content == "err"


def test_command_stdin_defaults_to_eof_and_allows_shell_input(tmp_path):
    runner = CommandRunner(tmp_path)
    input_path = tmp_path / "input"
    input_path.write_text("from file\n")
    command = "cat; cat < input; printf 'from pipe\\n' | cat"
    with input_path.open("rb") as source, os.fdopen(os.dup(0), "rb") as original:
        os.dup2(source.fileno(), 0)
        try:
            task = runner.start(2001, 123, command, str(tmp_path), 10000, now_ms() + 10000)
        finally:
            os.dup2(original.fileno(), 0)

    assert runner.wait(task, timeout=5) == 0
    assert runner.output_for(2001, 123, "stdout").content == "from file\nfrom pipe\n"
