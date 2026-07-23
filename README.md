# OpenEvent Cmd Module

[中文版](README_cn.md)

The OpenEvent Cmd module provides the `cmd.v1` protocol SDK and a Linux command
worker. Callers publish command requests to OpenEvent. The worker executes each
request as an asynchronous subprocess, writes stdout/stderr to local files,
publishes the final result back to the same channel, and supports offset-based
reads of output files through OpenEvent messages.

## Requirements

- Python 3.10 or later.
- `openevent-sdk>=0.4.1` importable in the current Python environment.
- A running OpenEvent server.
- A pre-created non-public channel with `protocol="cmd.v1"`.
- The cmd-worker principal and business caller principals must be members of the
  target channel.

This project does not create OpenEvent channels and does not manage tokens,
members, ACLs, or deployment.

## Install And Test

```bash
make test
make build
make install
```

`make test` uses the `openevent-sdk>=0.4.1` already installed in the current
Python environment. It does not install the SDK from a submodule or generate SDK
proto files. Build artifacts are written to `dist/`; temporary files are written
to `build/`.

## Security Boundary

The cmd worker executes the request `command` string through the Linux shell on
the worker host. Only connect the worker to trusted, non-public OpenEvent
channels.

The current implementation does not provide a shell sandbox, container
isolation, user switching, CPU/memory/process limits, filesystem isolation,
audit policy, or automatic output cleanup. stdout/stderr files are written under
the local `output_dir`; disk capacity and cleanup are deployment
responsibilities. The protocol also does not resolve conflicts from multiple
workers consuming the same cmd channel. Deployments should ensure there is only
one effective cmd worker consuming a channel at a time.

## Run The Worker

Copy [examples/cmd-worker.yaml](examples/cmd-worker.yaml), edit it for the
deployment, and start the worker:

```bash
cmd-worker --config cmd-worker.yaml
```

See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for the configuration format.
OpenEvent channels, tokens, members, and ACLs must be prepared separately.

## Documentation

- [docs/CMD_PROTOCOL.md](docs/CMD_PROTOCOL.md): `cmd.v1` event protocol.
- [docs/CONFIGURATION.md](docs/CONFIGURATION.md): worker configuration.
- [docs/SDK_USAGE.md](docs/SDK_USAGE.md): Python SDK usage.

## Current Capabilities And Boundaries

- Protocol version: `cmd.v1`
- Python version: `>=3.10`
- OpenEvent SDK: `openevent-sdk>=0.4.1`
- Default command timeout: `300000` ms
- Default concurrency limit: `8`
- Worker configuration must explicitly set `channel_ids`; the worker only
  processes messages from those channels.
- Configured channels must use `cmd.v1`, must not be public, and must include
  the cmd-worker principal as a member. A public channel makes worker startup
  fail.
- The worker fetches messages with OpenEvent `Fetch(only_my_recipient=false)`.
  The worker always passes configured `channel_ids` through the `channels`
  parameter.
- Command execution uses the `command` string through the Linux shell.
- `workdir` is optional; when present, it must be an absolute path.
- Command requests do not support separate `env` or `stdin` fields.
- stdout/stderr file paths are `<channel_id>/<cmd.run.request seq>.stdout` and
  `<channel_id>/<cmd.run.request seq>.stderr`.
- `cmd.run.result` inlines stdout/stderr content when each stream is at most
  1024 bytes; larger streams only include file metadata.
- Output content uses `content_encoding=utf-8` when it can be decoded as UTF-8;
  otherwise it uses `content_encoding=base64`.
- Partial output reads use
  `cmd.output.read.request(target_seq, stream, offset, nbytes)` and
  return content through `cmd.output.read.result.content`. The worker may return
  fewer than `nbytes` bytes to keep the result payload under the OpenEvent
  deployment limit.
- Any member of a channel can read command output in that channel.
- When execution concurrency is full, the worker continues consuming new
  messages and keeps an unbounded in-memory pending queue.
- When a kill request targets a pending task that has not started, the worker
  removes it from the queue and writes both `cmd.kill.result` and
  `cmd.run.result`.
- On startup, the worker scans from the beginning. Historical run requests that
  already have `cmd.run.accepted` but no `cmd.run.result` are completed with a
  failed result; run requests without `cmd.run.accepted` are processed normally.
- Invalid cmd payloads whose request kind can be identified are rejected through
  the corresponding result with `status=REJECTED` and `error_message`. Payloads
  damaged enough that their request kind cannot be identified are logged and
  ignored.
- Any member of a channel can kill running or pending tasks in that channel.

## Packages

- `openevent.cmd_sdk`: `cmd.v1` payload encoding, decoding, validation,
  publishing, and parsing helpers.
- `openevent.cmd_worker`: `cmd-worker` configuration, channel validation,
  command execution, and result publishing.
