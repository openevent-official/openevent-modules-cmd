"""Verify reusable build tools and current-artifact handling without a package index."""

import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]


class BuildTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="build-test-", dir=ROOT / "build")
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.helper = runpy.run_path(str(ROOT / "scripts/prepare_build_env.py"))
        self.globals = self.helper["ensure_environment"].__globals__

    def test_tools_environment_is_reused_and_rebuilt_for_python_changes(self):
        environment = self.workspace / "tools-env"
        identity = dict(executable="/python-one", real_executable="/python-one",
                        implementation="cpython", version="3.10.0")

        def create(path):
            (path / "bin").mkdir(parents=True)
            (path / "bin/python").touch()

        builder = Mock()
        builder.create.side_effect = create
        with patch.dict(self.globals, interpreter_identity=lambda: identity), \
                patch.object(self.globals["venv"], "EnvBuilder", return_value=builder):
            python = self.helper["ensure_environment"](environment)
            marker = environment / "installed-tool"
            marker.write_text("keep")
            self.assertEqual(self.helper["ensure_environment"](environment), python)
            self.assertEqual(marker.read_text(), "keep")
            self.assertEqual(builder.create.call_count, 1)

            for field, value in (("executable", "/python-two"), ("version", "3.10.1")):
                with self.subTest(field=field):
                    marker.write_text("old environment")
                    identity = dict(identity, **{field: value})
                    previous_creations = builder.create.call_count
                    self.helper["ensure_environment"](environment)
                    self.assertFalse(marker.exists())
                    self.assertTrue(python.exists())
                    self.assertEqual(builder.create.call_count, previous_creations + 1)

    def test_tool_preparation_installs_only_when_requirements_are_missing(self):
        backend = "cmd-build-backend>=2"
        extension = "cmd-build-extension>=3"
        required = {"build", backend, extension}
        for missing in (None, backend, extension):
            with self.subTest(missing=missing):
                installed = required - {missing}
                installations = []

                def install(requirements):
                    installations.append(set(requirements))
                    installed.update(requirements)

                def check_dependency(requirement):
                    return iter(()) if requirement in installed else iter([(requirement,)])

                def get_requires_for_build(distribution):
                    self.assertIn(backend, installed, "prepare the backend before calling its hook")
                    self.assertEqual(distribution, "wheel")
                    return {extension}

                builder = SimpleNamespace(build_system_requires={backend},
                                          get_requires_for_build=get_requires_for_build)
                frontend = SimpleNamespace(ProjectBuilder=lambda root: builder,
                                           check_dependency=check_dependency)
                with patch.dict(sys.modules, build=frontend), \
                        patch.dict(self.globals, version=lambda name: "1.0", install=install):
                    self.helper["prepare_tools"](self.workspace)

                self.assertEqual(installed, required)
                expected = [] if missing is None else [{"build", backend} if missing == backend else required]
                self.assertEqual(installations, expected)

    def test_failed_tool_preparation_removes_old_wheel_and_stops_installation(self):
        for name in ("Makefile", "build.sh"):
            shutil.copy2(ROOT / name, self.workspace / name)
        scripts = self.workspace / "scripts"
        scripts.mkdir()
        (scripts / "prepare_build_env.py").write_text("raise RuntimeError('tools unavailable')\n")
        (scripts / "install.py").write_text("from pathlib import Path\nPath('install-ran').touch()\n")
        dist = self.workspace / "dist"
        dist.mkdir()
        (dist / "openevent_modules_cmd-0.3.0-py3-none-any.whl").write_text("old artifact")
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", TMPDIR=str(self.workspace))
        result = subprocess.run(
            ["make", "install", "PYTHON=" + sys.executable], cwd=self.workspace,
            env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("tools unavailable", result.stdout)
        self.assertEqual(list(dist.glob("*.whl")), [])
        self.assertFalse((self.workspace / "install-ran").exists())
