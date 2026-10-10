"""Check E2E installation discovery with wheel-style distribution metadata."""

from importlib.metadata import PathDistribution
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from e2e.installed_worker import installed_worker_path


class E2EInstallationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="e2e-installation-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def install(self, prefix):
        site = prefix / "lib" / "python3.10" / "site-packages"
        metadata = site / "openevent_modules_cmd-0.2.0.dist-info"
        metadata.mkdir(parents=True)
        metadata.joinpath("METADATA").write_text(
            "Metadata-Version: 2.1\nName: openevent-modules-cmd\nVersion: 0.2.0\n"
        )
        metadata.joinpath("entry_points.txt").write_text(
            "[console_scripts]\ncmd-worker = openevent.cmd_worker.cli:main\n"
        )
        module = site / "openevent" / "cmd_worker" / "cli.py"
        module.parent.mkdir(parents=True)
        module.touch()
        script = prefix / "bin" / "cmd-worker"
        script.parent.mkdir()
        script.write_text(f"#!{sys.executable}\nprint('installed worker')\n")
        script.chmod(0o755)
        metadata.joinpath("RECORD").write_text(
            f"openevent/cmd_worker/cli.py,,\n{os.path.relpath(script, site)},,\n"
        )
        return PathDistribution(metadata), module, script

    def test_finds_normal_venv_and_user_installations_from_metadata(self):
        for layout in ("system", "venv", "user-base"):
            with self.subTest(layout=layout):
                location = self.root / layout
                package, module, script = self.install(location)
                python_prefix = self.root / "system" if layout == "user-base" else location
                with patch("e2e.installed_worker.distribution", return_value=package), \
                        patch.object(sys, "prefix", str(python_prefix)), \
                        patch.object(sys, "executable", str(python_prefix / "bin" / "python")), \
                        patch.dict(os.environ, {"PATH": str(self.root / "unrelated-bin")}):
                    found = installed_worker_path(module)
                self.assertEqual(found, script)
                result = subprocess.run([str(found)], check=True, text=True,
                                        stdout=subprocess.PIPE)
                self.assertEqual(result.stdout, "installed worker\n")

    def test_rejects_source_import_even_inside_the_python_prefix(self):
        prefix = self.root / "venv"
        package, _, _ = self.install(prefix)
        source = prefix / "checkout" / "src" / "openevent" / "cmd_worker" / "cli.py"
        source.parent.mkdir(parents=True)
        source.touch()
        with patch("e2e.installed_worker.distribution", return_value=package), \
                patch.object(sys, "prefix", str(prefix)):
            with self.assertRaisesRegex(RuntimeError, "imported from the installed Cmd wheel"):
                installed_worker_path(source)

    def test_rejects_missing_entry_point(self):
        package, module, _ = self.install(self.root / "venv")
        Path(package.locate_file("openevent_modules_cmd-0.2.0.dist-info/entry_points.txt")).unlink()
        with patch("e2e.installed_worker.distribution", return_value=package):
            with self.assertRaisesRegex(RuntimeError, "declare the cmd-worker entry point"):
                installed_worker_path(module)

    def test_requires_the_recorded_executable_instead_of_a_path_fallback(self):
        package, module, script = self.install(self.root / "user-base")
        other = self.root / "other-bin"
        other.mkdir()
        other.joinpath("cmd-worker").symlink_to(sys.executable)
        for missing in (False, True):
            with self.subTest(missing=missing):
                if missing:
                    script.unlink()
                else:
                    script.chmod(0o644)
                with patch("e2e.installed_worker.distribution", return_value=package), \
                        patch.dict(os.environ, {"PATH": str(other)}):
                    with self.assertRaisesRegex(RuntimeError, "one executable cmd-worker script"):
                        installed_worker_path(module)
