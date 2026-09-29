"""Check the installed SDK without importing a submodule source tree."""

from importlib.metadata import PackageNotFoundError, version

from packaging.version import InvalidVersion, Version


def require_sdk() -> None:
    requirement = "openevent-sdk>=0.11.1"
    try:
        installed = version("openevent-sdk")
        if Version(installed) >= Version("0.11.1"):
            return
    except (PackageNotFoundError, InvalidVersion):
        installed = "missing or invalid"
    raise RuntimeError(f"{requirement} must be installed in this Python environment; found {installed}")
