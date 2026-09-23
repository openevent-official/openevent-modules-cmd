"""Verify same-version installation and dependency handling with local wheels."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import venv
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def write_wheel(directory, name, version, files, requires=()):
    metadata = f"{name}-{version}.dist-info"
    contents = dict(files)
    contents[f"{metadata}/METADATA"] = (
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
        + "".join(f"Requires-Dist: {requirement}\n" for requirement in requires)
    )
    contents[f"{metadata}/WHEEL"] = (
        "Wheel-Version: 1.0\nGenerator: cmd-install-test\n"
        "Root-Is-Purelib: true\nTag: py3-none-any\n"
    )
    record = f"{metadata}/RECORD"
    contents[record] = "".join(f"{path},,\n" for path in [*contents, record])
    with zipfile.ZipFile(directory / f"{name}-{version}-py3-none-any.whl", "w") as archive:
        for member, content in contents.items():
            archive.writestr(member, content)


class InstallTest(unittest.TestCase):
    def setUp(self):
        build = ROOT / "build"
        build.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="install-test-", dir=build)
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        (self.workspace / "scripts").mkdir()
        self.wheels = self.workspace / "dist"
        self.wheels.mkdir()
        shutil.copyfile(ROOT / "scripts/install.py", self.workspace / "scripts/install.py")
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith("PIP_") and key != "PYTHONPATH"}
        self.environment.update(PYTHONDONTWRITEBYTECODE="1", PIP_NO_COMPILE="1",
                                PIP_NO_CACHE_DIR="1", PIP_CONFIG_FILE=os.devnull,
                                PIP_DISABLE_PIP_VERSION_CHECK="1", TMPDIR=str(self.workspace))

    def install(self, python=sys.executable, *arguments):
        return subprocess.run(
            [str(python), "-B", str(self.workspace / "scripts/install.py"),
             "--no-index", "--find-links", str(self.wheels), *arguments],
            env=self.environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )

    def test_replaces_same_version_and_only_changes_unsatisfied_dependencies(self):
        builder = venv.EnvBuilder(system_site_packages=True)
        location = self.workspace / "environment"
        builder.create(location)
        python = builder.ensure_directories(location).env_exe
        site = Path(subprocess.check_output(
            [python, "-B", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
            env=self.environment, text=True,
        ).strip())

        def project(content, obsolete=False, requires=("cmd_install_dependency>=1",)):
            files = {"cmd_install_project.py": content}
            if obsolete:
                files["cmd_install_obsolete.py"] = "old"
            write_wheel(self.wheels, "openevent_modules_cmd", "0.1.0", files, requires)

        write_wheel(self.wheels, "cmd_install_dependency", "1.0", {"cmd_install_dependency.py": "one"})
        project("old", obsolete=True)
        first = self.install(python)
        self.assertEqual(first.returncode, 0, first.stdout)
        dependency = site / "cmd_install_dependency.py"
        installed = site / "cmd_install_project.py"
        self.assertEqual(installed.read_text(), "old")
        mtime = dependency.stat().st_mtime_ns

        write_wheel(self.wheels, "cmd_install_dependency", "2.0", {"cmd_install_dependency.py": "two"})
        project("new")
        repeated = self.install(python)
        self.assertEqual(repeated.returncode, 0, repeated.stdout)
        self.assertEqual(installed.read_text(), "new")
        self.assertFalse((site / "cmd_install_obsolete.py").exists())
        self.assertEqual(dependency.read_text(), "one")
        self.assertEqual(dependency.stat().st_mtime_ns, mtime)

        project("new dependency", requires=("cmd_install_dependency>=2",))
        changed = self.install(python)
        self.assertEqual(changed.returncode, 0, changed.stdout)
        self.assertEqual(dependency.read_text(), "two")
        self.assertEqual(installed.read_text(), "new dependency")

        project("must not install", requires=("cmd_install_missing_dependency==1",))
        failed = self.install(python)
        self.assertNotEqual(failed.returncode, 0, failed.stdout)
        self.assertEqual(installed.read_text(), "new dependency")

    def test_requires_one_current_project_wheel(self):
        for versions in ((), ("0.1.0", "0.2.0")):
            with self.subTest(versions=versions):
                for version in versions:
                    write_wheel(self.wheels, "openevent_modules_cmd", version, {})
                result = self.install()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Exactly one Cmd wheel", result.stdout)

    def test_alternate_destination_rejected(self):
        result = self.install(sys.executable, "--target", str(self.workspace / "target"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("choose its interpreter with PYTHON", result.stdout)
        self.assertFalse((self.workspace / "target").exists())

    def test_failed_build_never_installs_old_artifact(self):
        shutil.copyfile(ROOT / "Makefile", self.workspace / "Makefile")
        write_wheel(self.wheels, "openevent_modules_cmd", "0.1.0", {})
        build = self.workspace / "build.sh"
        build.write_text("#!/bin/sh\nexit 1\n")
        build.chmod(0o755)
        marker = self.workspace / "install-ran"
        (self.workspace / "scripts/install.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
        )
        result = subprocess.run(
            ["make", "install", "PYTHON=" + sys.executable], cwd=self.workspace,
            env=self.environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(marker.exists())
