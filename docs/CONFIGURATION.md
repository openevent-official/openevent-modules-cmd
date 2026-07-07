# Configuration

[中文版](CONFIGURATION_cn.md)

This document defines the `cmd-worker` YAML configuration structure. The worker
loads the configuration through its startup argument:

```bash
cmd-worker --config cmd-worker.yaml
```

The repository includes a copyable example:
[../examples/cmd-worker.yaml](../examples/cmd-worker.yaml).

## Example

```yaml
protocol: cmd.v1

open_event:
  addr: 127.0.0.1:9527
  max_payload_bytes: 16777216

principal: 30001
token: token-xxx

channel_ids:
  - 2001
  - 2002

output_dir: cmd-worker-output
max_concurrent_tasks: 8
default_timeout_ms: 300000
fetch_idle_sleep_ms: 100
fetch_error_max_retries: 3
fetch_error_retry_sleep_ms: 1000
```

Use a single-item list when the worker handles one channel:

```yaml
channel_ids:
  - 2001
```

## Fields

| Field | Required | Default | Description |
| --- | --- | --- | --- |
| `protocol` | yes | none | Must be `cmd.v1`. |
| `open_event.addr` | yes | none | OpenEvent server address. |
| `open_event.max_payload_bytes` | no | `16777216` | OpenEvent deployment payload size limit. Must be a positive integer. The worker uses it to keep result payloads under the deployment limit. |
| `principal` | yes | none | OpenEvent principal used by cmd-worker. Must be a positive integer. |
| `token` | yes | none | OpenEvent token used by cmd-worker. Must be a non-empty string. |
| `channel_ids` | yes | none | Channel ids handled by cmd-worker. Must be a non-empty array of positive integers; `0` and duplicates are not allowed. |
| `output_dir` | no | `cmd-worker-output` | Local output root. stdout/stderr files are written under this directory; cleanup and capacity protection are deployment responsibilities. |
| `max_concurrent_tasks` | no | `8` | Maximum number of concurrently running command tasks. Must be a positive integer. |
| `default_timeout_ms` | no | `300000` | Default command timeout. Must be a positive integer. Used when a request omits `timeout_ms`, based on the worker configuration active when the request is handled. |
| `fetch_idle_sleep_ms` | no | `100` | Sleep time used by the main scheduling loop when idle. Must be a positive integer. |
| `fetch_error_max_retries` | no | `3` | Maximum retry count after any worker `Fetch` call first fails. Must be a positive integer. |
| `fetch_error_retry_sleep_ms` | no | `1000` | Sleep time before each retry after a worker `Fetch` call fails. Must be a positive integer. |

Unknown configuration fields are configuration errors.

## Channel Requirements

Every channel listed in `channel_ids` must satisfy the channel rules from the
`cmd.v1` protocol document:

- The channel protocol is `cmd.v1`.
- The channel visibility is not public.
- The cmd-worker principal is a channel member.
- Business caller principals are channel members.
- The channel description follows the `cmd.v1` protocol document.

The worker only processes channels listed in `channel_ids`. A channel not listed
there is outside this worker's scope even if its protocol is `cmd.v1`.

## Runtime Notes

- `output_dir` may be relative or absolute. Relative paths are resolved against
  the current working directory used to start `cmd-worker`.
- The worker creates and writes to `output_dir` during startup. If the directory
  cannot be created or written, startup fails with a configuration/environment
  error.
- Each channel writes output under `output_dir/<channel_id>/`. Output files from
  different channels are never mixed.
- The current implementation does not automatically clean output files and does
  not limit stdout/stderr file size for individual commands.
