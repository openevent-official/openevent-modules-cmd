# Configuration

[中文版](CONFIGURATION_cn.md)

Use `--config` to specify a YAML configuration file:

```bash
cmd-worker --config cmd-worker.yaml
```

Example file: [cmd-worker.yaml](../examples/cmd-worker.yaml).

## Example

```yaml
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

## Fields

| Field | Required | Default | Description |
| --- | --- | --- | --- |
| `open_event.addr` | yes | none | OpenEvent server address. |
| `open_event.max_payload_bytes` | no | `16777216` | OpenEvent deployment payload size limit. Must be an integer of at least `16384` (16 KiB). Every accepted/result payload is checked before publication; partial reads reduce their return size to fit. |
| `principal` | yes | none | OpenEvent principal used by cmd-worker. Must be a positive integer. |
| `token` | yes | none | OpenEvent token used by cmd-worker. Must be a non-empty string. |
| `channel_ids` | yes | none | Channel ids handled by the worker. Must be a non-empty array of distinct positive integers. |
| `output_dir` | no | `cmd-worker-output` | Local root directory for stdout/stderr files. |
| `max_concurrent_tasks` | no | `8` | Maximum number of concurrently running command tasks. Must be a positive integer. |
| `default_timeout_ms` | no | `300000` | Timeout when the request omits `timeout_ms`. Integer range: `1..18446744073709551615` (`2**64-1`). |
| `fetch_idle_sleep_ms` | no | `100` | Milliseconds to wait when the scheduling loop is idle. Must be a positive integer. |
| `fetch_error_max_retries` | no | `3` | Maximum retry count after each `Fetch` call first raises an RPC error. Must be a positive integer. |
| `fetch_error_retry_sleep_ms` | no | `1000` | Milliseconds to wait before each retry after a `Fetch` RPC error. Must be a positive integer. |

Unknown configuration fields are configuration errors.

## Channel Requirements

Every channel listed in `channel_ids` must satisfy the
[protocol channel rules](CMD_PROTOCOL.md#1-channel). Startup checks readability,
protocol, visibility, and worker membership for every channel;
any failure stops startup.

## Runtime Notes

- `output_dir` may be relative or absolute. Relative paths are resolved against
  the current working directory used to start `cmd-worker`.
- Startup creates and checks `output_dir`; failure to create or write to it causes
  a configuration/environment error and exit.
- Output is written under `output_dir/<channel_id>/`, without automatic cleanup or
  file size limits. Cleanup and capacity protection are deployment responsibilities.
- When concurrency is full, the worker continues consuming messages into an
  in-memory queue. The pending queue and unresolved requests during recovery scanning
  have no total size limit. The worker does not keep completed task identifiers or
  request records in memory.

## Task Process Lifecycle

Each command starts a shell as the leader of a separate process group. After the
shell exits, the worker first sends SIGKILL to that group to clean up remaining
members, then reaps the shell and completes the task.

Group cleanup after natural exit does not change the result to KILLED; the shell
exit code still determines the result. An already established timeout or kill
reason is preserved, following the [run result protocol](CMD_PROTOCOL.md#5-cmdrunresult).
Normal completion, timeout, kill, and catchable worker exit all use the same cleanup
and reap flow.

Cleanup covers only the process group created for that task. Processes that leave
the group through `setsid`/`setpgid` are not tracked. The worker does not provide
cgroups or full process-tree management.

## Recovery And Exit

At startup the worker reads history from the beginning, fixing the recovery range
at the message tail returned by its first Fetch. Newer messages are handled during
normal operation. It does not execute commands or publish results before the scan finishes.

Historical requests are handled in seq order:

- Requests with a corresponding result are skipped.
- A run with accepted but no result receives `RECOVERY_FAILED`; its command is not rerun.
- A run with neither accepted nor result is queued normally and checked for expiry before startup.
- A kill or output read without its corresponding result is processed again using
  the task state and retained output files available during recovery. Output reads
  do not reconstruct a file snapshot from before the crash.

When a run request omits `timeout_ms`, recovery uses the current
`default_timeout_ms`, including the result field in a generated `RECOVERY_FAILED`.
An explicit request timeout keeps its original value.

Fetch RPC errors are retried as configured above; exhaustion makes the worker exit.
The worker also exits when accepted/result publication fails definitively or remains
unconfirmed after retry. Ordinary program and stop exceptions propagate directly
instead of entering RPC retries.
These exit paths, Ctrl+C, and SIGTERM clean up the current worker's unfinished
tasks and reap their shells as defined in [Task Process Lifecycle](#task-process-lifecycle).
Requests without final results are handled by recovery at the next startup.
SIGINT/SIGTERM received during cleanup do not interrupt cleanup of the remaining tasks.
When a stop signal triggers exit, the CLI preserves the exit code corresponding to the first stop signal.

Forced worker termination or host failure cannot guarantee process cleanup.
Recovery does not terminate OS processes left by historical workers or guarantee
exactly-once command execution.
