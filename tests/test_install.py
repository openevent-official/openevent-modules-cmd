"""Observable results of building, installing and cleaning Cmd."""

import os
from pathlib import Path
import shutil
import subprocess
import venv
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]


def write_wheel(directory, name, version, files, requires=(), extras=()):
    metadata = f"{name}-{version}.dist-info"
    contents = dict(files)
    contents[f"{metadata}/METADATA"] = (
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
        + "".join(f"Provides-Extra: {extra}\n" for extra in extras)
        + "".join(f"Requires-Dist: {requirement}\n" for requirement in requires)
    )
    contents[f"{metadata}/WHEEL"] = (
        "Wheel-Version: 1.0\nGenerator: cmd-install-test\n"
        "Root-Is-Purelib: true\nTag: py3-none-any\n"
    )
    record = f"{metadata}/RECORD"
    contents[record] = "".join(f"{path},,\n" for path in [*contents, record])
    wheel = directory / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for member, content in contents.items():
            archive.writestr(member, content)
    return wheel


@pytest.fixture
def python(tmp_path):
    directory = tmp_path / "chosen environment"
    venv.EnvBuilder(system_site_packages=True).create(directory)
    return directory / "bin" / "python"


@pytest.fixture
def pip_env(tmp_path):
    environment = {k: v for k, v in os.environ.items() if not k.startswith("PIP_")}
    environment.update(PIP_NO_INDEX="1", PIP_NO_CACHE_DIR="1", PIP_CONFIG_FILE=os.devnull, TMPDIR=str(tmp_path),
                       PIP_DISABLE_PIP_VERSION_CHECK="1", PYTHONDONTWRITEBYTECODE="1")
    return environment


def test_install_replaces_same_version_and_only_changes_unsatisfied_dependencies(tmp_path, python, pip_env):
    wheels = tmp_path / "wheelhouse"
    wheels.mkdir()
    pip_env["PIP_FIND_LINKS"] = wheels.as_uri()
    site = Path(subprocess.check_output(
        [python, "-B", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        env=pip_env, text=True,
    ).strip())

    def install(value, dependency="cmd_install_dependency>=1", *, obsolete=False, test_env=False):
        files = {"openevent/cmd_sdk/__init__.py": f"value = {value!r}\n"}
        if obsolete:
            files["openevent/cmd_sdk/obsolete.py"] = ""
        wheel = write_wheel(tmp_path, "openevent_modules_cmd", "0.4.0", files,
                            requires=(dependency, 'cmd_test_dependency==1; extra == "test"'),
                            extras=("test",))
        options = ["--test-env"] if test_env else []
        subprocess.run([python, "-B", ROOT / "scripts/install.py", *options, wheel],
                       cwd=tmp_path, env=pip_env, check=True)

    project = site / "openevent/cmd_sdk/__init__.py"
    dependency = site / "cmd_install_dependency.py"
    test_dependency = site / "cmd_test_dependency.py"
    write_wheel(wheels, "cmd_install_dependency", "1.0", {dependency.name: "one"})
    write_wheel(wheels, "cmd_test_dependency", "1.0", {test_dependency.name: ""})
    install("old", obsolete=True)
    assert project.read_text() == "value = 'old'\n"
    assert dependency.read_text() == "one"
    assert not test_dependency.exists()
    mtime = dependency.stat().st_mtime_ns

    write_wheel(wheels, "cmd_install_dependency", "2.0", {dependency.name: "two"})
    install("new", test_env=True)
    assert project.read_text() == "value = 'new'\n"
    assert not (project.parent / "obsolete.py").exists()
    assert test_dependency.is_file()
    assert dependency.read_text() == "one"
    assert dependency.stat().st_mtime_ns == mtime

    install("needs newer dependency", dependency="cmd_install_dependency>=2")
    assert dependency.read_text() == "two"


@pytest.mark.parametrize("option", ["prefix", "user"])
def test_install_rejects_redirected_pip_conf_before_build(tmp_path, python, pip_env, option):
    for name in ("Makefile", "build.sh", "pyproject.toml"):
        shutil.copy2(ROOT / name, tmp_path / name)
    shutil.copytree(ROOT / "scripts", tmp_path / "scripts")
    dist = tmp_path / "dist"
    dist.mkdir()
    previous = dist / "openevent_modules_cmd-0.3.0-py3-none-any.whl"
    previous.touch()
    outside = tmp_path / "redirected"
    pip_env["PYTHONUSERBASE"] = str(outside)
    value = outside if option == "prefix" else "true"
    (python.parent.parent / "pip.conf").write_text(f"[install]\n{option} = {value}\n")
    empty_config = tmp_path / "empty.conf"
    empty_config.touch()
    pip_env["PIP_CONFIG_FILE"] = str(empty_config)
    result = subprocess.run(["make", "install", f"PYTHON={python}"], cwd=tmp_path, env=pip_env,
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert result.returncode != 0
    assert f"--{option}" in result.stdout or f"install.{option}" in result.stdout, result.stdout
    assert previous.exists()
    assert not outside.exists()


def test_clean_preserves_other_artifacts_and_local_environment(tmp_path):
    shutil.copy2(ROOT / "Makefile", tmp_path / "Makefile")
    keep = ("dist/other-1-py3-none-any.whl", "build/wheelhouse/dependency.whl", "build/venv/pyvenv.cfg")
    generated = ("dist/openevent_modules_cmd-0.4.0-py3-none-any.whl", "build/tmp/work", "build/unit/results")
    for name in (*keep, *generated):
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True, parents=True)
        path.touch()
    subprocess.run(["make", "clean"], cwd=tmp_path, check=True)
    for name in keep:
        assert (tmp_path / name).exists()
    for name in generated:
        assert not (tmp_path / name).exists()
