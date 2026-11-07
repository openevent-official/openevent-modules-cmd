"""Reuse a project-local build environment for the selected Python interpreter."""

from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import shutil
import subprocess
import sys
import venv


ROOT = Path(__file__).resolve().parents[1]


def interpreter_identity() -> dict[str, str]:
    return {
        "executable": str(Path(sys.executable).absolute()),
        "real_executable": str(Path(sys.executable).resolve()),
        "implementation": sys.implementation.name,
        "version": sys.version,
    }


def ensure_environment(environment: Path) -> Path:
    identity = interpreter_identity()
    stamp = environment / "interpreter.json"
    python = environment / "bin/python"
    try:
        previous = json.loads(stamp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = None
    if previous != identity or not python.exists():
        if environment.exists():
            shutil.rmtree(environment)
        venv.EnvBuilder(with_pip=True).create(environment)
        stamp.write_text(json.dumps(identity), encoding="utf-8")
    return python


def install(requirements: set[str]) -> None:
    subprocess.run(
        [sys.executable, "-B", "-m", "pip", "install", "--no-compile", *sorted(requirements)],
        check=True,
    )


def ensure_requirements(requirements: set[str]) -> None:
    from build import check_dependency

    if any(list(check_dependency(requirement)) for requirement in requirements):
        install(requirements)


def prepare_tools(root: Path) -> None:
    # Bootstrap the frontend before using its dependency and pyproject readers.
    try:
        version("build")
    except PackageNotFoundError:
        install({"build"})

    from build import ProjectBuilder

    builder = ProjectBuilder(str(root))
    requirements = {"build"} | builder.build_system_requires
    ensure_requirements(requirements)
    requirements |= builder.get_requires_for_build("wheel")
    ensure_requirements(requirements)


def main() -> None:
    if sys.argv[1:] == ["--dependencies"]:
        prepare_tools(ROOT)
    else:
        python = ensure_environment(ROOT / "build/tools-env")
        subprocess.run([str(python), "-B", __file__, "--dependencies"], check=True)


if __name__ == "__main__":
    main()
