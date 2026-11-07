"""Keep SDK preflight checks aligned with the package dependency declaration."""

from contextlib import redirect_stderr
import io
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from openevent.cmd_sdk.dependencies import SDK_REQUIREMENT, require_sdk


ROOT = Path(__file__).resolve().parents[1]


class DependencyTest(unittest.TestCase):
    def test_runtime_check_uses_requirement_and_allows_qualifying_prereleases(self):
        cases = (
            (SDK_REQUIREMENT, "0.12.0", False),
            (SDK_REQUIREMENT, "0.13.0", True),
            (SDK_REQUIREMENT, "0.14.0rc1", True),
            ("openevent-sdk>=0.14", "0.13.0", False),
        )
        for requirement, installed, accepted in cases:
            with self.subTest(requirement=requirement, installed=installed), \
                    patch("openevent.cmd_sdk.dependencies.SDK_REQUIREMENT", requirement), \
                    patch("openevent.cmd_sdk.dependencies.version", return_value=installed):
                if accepted:
                    require_sdk()
                else:
                    with self.assertRaises(RuntimeError):
                        require_sdk()

    def test_documentation_check_rejects_runtime_requirement_drift(self):
        check = runpy.run_path(str(ROOT / "scripts/check_docs.py"))["main"]
        errors = io.StringIO()
        with patch.dict(check.__globals__, SDK_REQUIREMENT="openevent-sdk>=99999"), \
                redirect_stderr(errors):
            self.assertEqual(check(), 1)
        self.assertIn("SDK runtime check uses openevent-sdk>=99999", errors.getvalue())
        self.assertIn(f"expected {SDK_REQUIREMENT} from pyproject.toml", errors.getvalue())

    def test_e2e_checks_current_source_requirement_before_building(self):
        with tempfile.TemporaryDirectory(prefix="dependency-test-", dir=ROOT / "build") as temporary:
            workspace = Path(temporary)
            for name in ("Makefile", "test-e2e.sh"):
                shutil.copy2(ROOT / name, workspace / name)
            package = workspace / "src/openevent/cmd_sdk"
            package.mkdir(parents=True)
            (package / "__init__.py").touch()
            checker = (ROOT / "src/openevent/cmd_sdk/dependencies.py").read_text()
            (package / "dependencies.py").write_text(checker.replace(SDK_REQUIREMENT, "openevent-sdk>=99999"))
            build = workspace / "build.sh"
            build.write_text("#!/bin/sh\ntouch build-ran\nexit 1\n")
            build.chmod(0o755)
            environment = dict(os.environ, OPENEVENT_SERVER_BIN=sys.executable,
                               PYTHONDONTWRITEBYTECODE="1", TMPDIR=str(workspace))
            environment.pop("PYTHONPATH", None)
            environment.pop("PYTHONHOME", None)
            result = subprocess.run(
                ["make", "e2e", "PYTHON=" + sys.executable], cwd=workspace, env=environment,
                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("openevent-sdk>=99999 must be installed", result.stdout)
            self.assertFalse((workspace / "build-ran").exists(), result.stdout)
