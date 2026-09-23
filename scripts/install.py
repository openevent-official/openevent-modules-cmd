"""Install the unique project wheel produced by the successful build prerequisite."""

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def main():
    arguments = sys.argv[1:]
    destinations = ("--target", "--prefix", "--root")
    if (any(argument.startswith("-t") or any(
            argument == option or argument.startswith(option + "=")
            for option in destinations) for argument in arguments)
            or any(os.environ.get(name) for name in ("PIP_TARGET", "PIP_PREFIX", "PIP_ROOT"))):
        sys.exit("make install installs into the selected Python environment; "
                 "choose its interpreter with PYTHON instead of --target, --prefix, or --root")

    wheels = list((ROOT / "dist").glob("openevent_modules_cmd-*.whl"))
    if len(wheels) != 1:
        sys.exit("Exactly one Cmd wheel from a successful make build is required")

    temporary = ROOT / "build" / "install-tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    environment = dict(
        os.environ, PYTHONDONTWRITEBYTECODE="1", PIP_NO_COMPILE="1",
        PIP_NO_CACHE_DIR="1", PIP_DISABLE_PIP_VERSION_CHECK="1", TMPDIR=str(temporary),
    )
    pip = [sys.executable, "-B", "-m", "pip", "install", "--no-compile"]
    # Resolve the wheel's dependency metadata normally before replacing this project.
    subprocess.run(pip + arguments + [str(wheels[0])], env=environment, check=True)
    subprocess.run(pip + ["--force-reinstall", "--no-deps"] + arguments + [str(wheels[0])],
                   env=environment, check=True)


if __name__ == "__main__":
    main()
