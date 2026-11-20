# OpenEvent Cmd Module

[中文版](README_cn.md)

The OpenEvent Cmd module provides a `cmd.v4` SDK and a Linux command worker.
Messages run or terminate commands, store stdout/stderr in local files, publish
results, and read byte ranges from output files.

## Requirements

- Linux and Python 3.10 or later.
- `openevent-sdk>=0.13.0` installed in the Python environment.
- A running OpenEvent server with channels, tokens, and membership configured.
  Channels must meet the [protocol requirements](docs/CMD_PROTOCOL.md#1-channel).

Commands execute directly through the Linux shell on the worker host, without
isolation, resource limits, or auditing.

## Build And Install

Run Make from the project root. Activate a writable virtual environment with pip installed. Make uses its `python3` by default. You can also set `PYTHON` to the virtual environment's interpreter. This example creates a virtual environment and installs Cmd.

```bash
python3 -B -m venv build/venv
source build/venv/bin/activate
make install
```

| Command | Purpose |
| --- | --- |
| `make build` | Default target. Build the current source in an isolated environment using `pyproject.toml`, then move the resulting wheel into `dist/`. |
| `make install` | Install the wheel from this build into the selected environment. Replace Cmd even at the same version and reuse dependencies that meet its requirements. |
| `make test` | Install the wheel from this build with its test dependencies and run the unit tests with the same Python. Pass pytest options with `TEST_ARGS`, for example `TEST_ARGS=-q`. |
| `make clean` | Remove Cmd wheels, caches and temporary test files. Preserve virtual environments, offline dependencies and other projects' artifacts. |

Run these commands sequentially in the same working directory. The selected virtual environment determines the install location. Before building, both `make install` and `make test` check pip's environment variables and configuration files. If a setting specifies an install prefix, enables user-site installation, or selects another install location, the command reports the conflicting option.

The installation tests create a temporary virtual environment and use the base interpreter's pip, so the base interpreter must also have pip installed. Temporary test files go under `build/unit/`.

For offline use, populate `build/wheelhouse/` with wheels for the target Python and platform covering all build and runtime requirements (including the OpenEvent SDK) and their dependencies. Include test dependencies when running tests. The following command installs from that directory.

```bash
PIP_NO_INDEX=1 PIP_FIND_LINKS=build/wheelhouse make install
```

Replace `install` with `test` to run the tests offline.

## Run The Worker

Copy [examples/cmd-worker.yaml](examples/cmd-worker.yaml), edit it for your
deployment, and start the worker:

```bash
cmd-worker --config cmd-worker.yaml
```

The worker only handles configured `channel_ids`. See the
[configuration document](docs/CONFIGURATION.md) for fields and runtime behavior.

## Documentation

- [Protocol](docs/CMD_PROTOCOL.md): message fields, task states,
  output encoding, and channel requirements.
- [Configuration and runtime notes](docs/CONFIGURATION.md): configuration, process
  management, recovery, and exit.
- [SDK usage](docs/SDK_USAGE.md): Python publishing, parsing, and error handling
  interfaces.
