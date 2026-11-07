"""Check the installed SDK without importing a submodule source tree."""

from importlib.metadata import PackageNotFoundError, version

from packaging.requirements import Requirement
from packaging.version import InvalidVersion, Version

SDK_REQUIREMENT = "openevent-sdk>=0.13.0"


def require_sdk() -> None:
    requirement = Requirement(SDK_REQUIREMENT)
    try:
        installed = version(requirement.name)
        if requirement.specifier.contains(Version(installed), prereleases=True):
            return
    except (PackageNotFoundError, InvalidVersion):
        installed = "missing or invalid"
    raise RuntimeError(f"{requirement} must be installed in this Python environment; found {installed}")
