"""Locate the installed worker without assuming a Python installation layout."""

from importlib.metadata import distribution
import os
from pathlib import Path


def installed_worker_path(module_file):
    package = distribution("openevent-modules-cmd")
    files = package.files or ()
    module = next((item for item in files
                   if str(item) == "openevent/cmd_worker/cli.py"), None)
    if (module is None or Path(module_file).resolve()
            != Path(package.locate_file(module)).resolve()):
        raise RuntimeError("worker must be imported from the installed Cmd wheel")
    if not any(entry.group == "console_scripts" and entry.name == "cmd-worker"
               and entry.value == "openevent.cmd_worker.cli:main"
               for entry in package.entry_points):
        raise RuntimeError("installed Cmd wheel must declare the cmd-worker entry point")
    scripts = [Path(package.locate_file(item)).resolve()
               for item in files if item.name == "cmd-worker"]
    if len(scripts) != 1 or not scripts[0].is_file() or not os.access(scripts[0], os.X_OK):
        raise RuntimeError("installed Cmd wheel must provide one executable cmd-worker script")
    return scripts[0]
