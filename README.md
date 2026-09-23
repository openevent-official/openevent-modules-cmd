# OpenEvent Cmd Module

[中文版](README_cn.md)

The OpenEvent Cmd module provides a `cmd.v1` SDK and a Linux command worker.
Messages run or terminate commands, store stdout/stderr in local files, publish
results, and read byte ranges from output files.

## Requirements

- Linux and Python 3.10 or later.
- Dependencies from the package metadata installed in the deployment Python
  environment, including `openevent-sdk>=0.8.1`.
- A running OpenEvent server with channels, tokens, and membership prepared.
- Trusted, non-public channels and one worker per channel, following the
  [protocol channel requirements](docs/CMD_PROTOCOL.md#1-channel).

This module does not manage OpenEvent channels, tokens, ACLs, or deployment.
Commands execute directly through the Linux shell on the worker host. Isolation,
resource limits, and auditing are not provided. Output retention and capacity
protection are deployment responsibilities.

## Install And Test

```bash
make install
make test
make check-docs
```

`make install` builds and installs the unique wheel from that build. It replaces
this project even when its version is unchanged. Dependencies are resolved from
wheel metadata; already satisfied dependencies are not forcibly reinstalled with
the project. Choose the target environment with `PYTHON=/path/to/venv/bin/python`.
Use `make build` to build without installing.

`make test` uses the SDK installed in the selected Python environment and runs
unit and installation regression tests. It does not load SDK submodule source or
generate SDK proto files. Tests may use a separate environment. Real-server E2E
tests are not implemented; `make e2e` reports that state and exits nonzero.
Final artifacts go into `dist/`; temporary files, caches, and test data go into
`build/`.

## Run The Worker

Copy [examples/cmd-worker.yaml](examples/cmd-worker.yaml), edit it for your
deployment, and start the worker:

```bash
cmd-worker --config cmd-worker.yaml
```

The worker only handles configured `channel_ids`. Startup validates the
configuration, SDK, and channel environment. See the
[configuration document](docs/CONFIGURATION.md) for the format.

## Documentation

- [Protocol](docs/CMD_PROTOCOL.md): authoritative message fields, task states,
  output encoding, and channel requirements.
- [Configuration and runtime notes](docs/CONFIGURATION.md): configuration fields,
  defaults, task process lifecycle, startup recovery, and the exit contract.
- [SDK usage](docs/SDK_USAGE.md): Python publishing, parsing, and error handling
  interfaces.

## Runtime Boundaries

After the shell exits, the worker cleans up remaining group members, then reaps
the shell and completes the task. See
[Task Process Lifecycle](docs/CONFIGURATION.md#task-process-lifecycle) for its scope.
Startup recovery and fatal communication failure behavior are defined in
[Recovery And Exit](docs/CONFIGURATION.md#recovery-and-exit).
When concurrency is full, it continues consuming messages into an in-memory queue.
The queue and historical result indexes have no total size limit.

## Packages

- `openevent.cmd_sdk`: `cmd.v1` payload encoding, decoding, validation, publishing,
  and parsing helpers.
- `openevent.cmd_worker`: configuration, channel validation, command scheduling,
  execution, and result publishing.
