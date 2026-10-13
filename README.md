# OpenEvent Cmd Module

[中文版](README_cn.md)

The OpenEvent Cmd module provides a `cmd.v3` SDK and a Linux command worker.
Messages run or terminate commands, store stdout/stderr in local files, publish
results, and read byte ranges from output files.

## Requirements

- Linux and Python 3.10 or later.
- `openevent-sdk>=0.11.1` installed in the Python environment.
- A running OpenEvent server with channels, tokens, and membership configured.
  Channels must meet the [protocol requirements](docs/CMD_PROTOCOL.md#1-channel).

Commands execute directly through the Linux shell on the worker host, without
isolation, resource limits, or auditing.

## Install And Test

```bash
make install
make test
make check-docs
```

Select the installation and test environment with `PYTHON=/path/to/venv/bin/python`.
`make install` builds and replaces this package, even at the same version, and
installs only missing or incompatible dependencies. Use `make build` to build without installing.
User installations are supported with `INSTALL_ARGS=--user` for `make install` and `make e2e`.

`make test` runs unit and installation regression tests. End-to-end tests require
an OpenEvent server from a successful current build:

```bash
OPENEVENT_SERVER_BIN=/path/to/openevent_server make e2e
```

`make e2e` builds and installs the current Cmd package in the selected environment,
then starts a real server and worker. Both test targets use that environment's
installed SDK and accept unittest arguments through `TEST_ARGS`. End-to-end case
data and logs are in `build/e2e/cases/`.
Final artifacts go into `dist/`; temporary files, caches, and test data go into
`build/`.

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
