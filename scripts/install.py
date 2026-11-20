"""Install the Cmd wheel supplied by the build in the selected Python environment."""

from pathlib import Path
import subprocess
import sys

from pip._internal.commands import create_command


def check_environment():
    parser = create_command("install").parser
    options, _ = parser.parse_args([])
    for flag in ("--python", "--target", "--prefix", "--root", "--user"):
        option = parser.get_option(flag)
        if option and getattr(options, option.dest):
            sys.exit(f"pip has {flag} set in the environment or pip configuration. "
                     "Remove it to install into the selected virtual environment.")


def main():
    if sys.argv[1] == "--check-environment":
        check_environment()
        return

    wheel = str(Path(sys.argv[-1]).resolve())
    requirement = f"{wheel}[test]" if sys.argv[1] == "--test-env" else wheel
    pip = [sys.executable, "-B", "-m", "pip", "install", "--no-compile"]
    subprocess.run(pip + [requirement], check=True)
    subprocess.run(pip + ["--force-reinstall", "--no-deps", wheel], check=True)


if __name__ == "__main__":
    main()
