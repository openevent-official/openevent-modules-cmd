# Cmd Protocol cmd.v1

[中文版](CMD_PROTOCOL_cn.md)

> Status: v0.1 initial release
> Scope: Linux command task payloads and channel descriptions for OpenEvent
> channels with `protocol="cmd.v1"`.

## 1. Channel

Every cmd channel must use:

```text
protocol = "cmd.v1"
```

`description` must be a JSON string:

```json
{
  "version": "v1",
  "updated_at_ms": 1710000000000,
  "metadata": {}
}
```

Field rules:

- `version`: string, currently fixed to `v1`.
- `updated_at_ms`: integer Unix timestamp in milliseconds.
- `metadata`: optional object for static deployment or business-domain
  metadata.

Cmd channels must not use public visibility. Business caller principals and the
cmd-worker principal must all be channel members. `cmd.v1` does not resolve
conflicts from multiple workers. Deployments must ensure there is only one
effective cmd-worker process consuming the same channel at a time.

## 2. Common Rules

All `cmd.v1` payloads are UTF-8 JSON objects. Unknown fields are invalid.

This document uses `max_payload_bytes` for the OpenEvent deployment message
payload size limit. That limit is deployment-specific; the reference
implementation default is 16 MiB. All cmd protocol limits related to message
size are computed against the actual deployment value.

Common fields:

- `kind`: required. One of `cmd.run.request`, `cmd.run.result`,
  `cmd.run.accepted`, `cmd.kill.request`, `cmd.kill.result`,
  `cmd.output.read.request`, or `cmd.output.read.result`.
- `ts_ms`: required. Unix timestamp in milliseconds written by the payload
  producer.

OpenEvent top-level `principal`, `channel_id`, `seq`, `ts_ms`, and `recipients`
carry identity, server write time, and routing information. They are not
duplicated inside payloads. OpenEvent top-level `EventMessage.ts_ms` is the Unix
millisecond timestamp generated when the server receives the message publish
request. When the cmd protocol computes command timeouts, it must use the
OpenEvent top-level `EventMessage.ts_ms` of the corresponding
`cmd.run.request`, not the payload `ts_ms`.

When a payload is invalid but the request kind can be identified, the worker
must write the corresponding result with `status="REJECTED"`. If the payload is
too damaged to identify the request kind, the worker can only log the error and
ignore it. Every result with `status="REJECTED"` must include `error_message` to
explain the rejection.

## 3. `cmd.run.request`

```json
{
  "kind": "cmd.run.request",
  "request_id": "cmd:agent:s1:wal:100:0",
  "ts_ms": 1710000000000,
  "command": "echo hello",
  "workdir": "/absolute/path/to/workdir",
  "timeout_ms": 300000
}
```

Rules:

- OpenEvent `principal` is the business caller principal.
- OpenEvent `recipients` must be empty.
- `request_id` is required. It is a caller-generated idempotency identifier and
  may contain only letters, digits, dots, underscores, colons, and hyphens.
- `command` is required. It must be a non-empty string containing the full Linux
  command line to execute.
- The worker executes `command` through the Linux shell; by default this is
  equivalent to `/bin/sh -c <command>`.
- `workdir` is optional. It is a non-empty string describing the command working
  directory. If absent, the worker uses its current working directory.
- When present, `workdir` must be an absolute path.
- If `workdir` is absolute but does not exist, is not a directory, cannot be
  entered due to permissions, or causes the shell process to fail to start, the
  payload itself is not invalid. The worker writes
  `cmd.run.result(status=START_FAILED)` with `error_message`.
- `timeout_ms` is optional and must be a positive integer. If absent, the worker
  uses the configured default timeout.
- Missing `timeout_ms` always uses the default timeout configuration active when
  the worker handles that request. This also applies when startup recovery
  handles historical run requests that were not accepted.
- The timeout deadline is computed from the OpenEvent top-level
  `EventMessage.ts_ms` of this `cmd.run.request` plus `timeout_ms`. If
  `timeout_ms` is absent, the configured default timeout is used. The payload
  `ts_ms` is not the timeout start point.
- `cmd.run.request` does not support separate `env` or `stdin` fields.
- The OpenEvent `seq` of this message is the command task id.

## 4. `cmd.run.accepted`

When the worker is ready to start a valid, non-expired `cmd.run.request`, it must
write an accepted message before starting the command process. This message
means the worker has taken ownership of the run request and is about to start
it. Later, the worker either writes the final `cmd.run.result`, or startup
recovery completes it with a failed result.

```json
{
  "kind": "cmd.run.accepted",
  "prev_seq": 12345,
  "ts_ms": 1710000000050
}
```

Rules:

- OpenEvent `principal` is the cmd-worker principal.
- OpenEvent `recipients` is the sender principal of the original
  `cmd.run.request`.
- `prev_seq` is required and must equal the OpenEvent seq of the corresponding
  run request.
- `cmd.run.accepted` must be written before the command process starts.
- If the run request is invalid, the worker does not write `cmd.run.accepted`;
  it writes `cmd.run.result(status=REJECTED)` directly.
- If the run request expires before obtaining an execution slot, the worker does
  not write `cmd.run.accepted`; it writes `cmd.run.result(status=TIMEOUT)`
  directly.
- If the run request is killed while still pending, the worker does not write
  `cmd.run.accepted`; it writes `cmd.run.result(status=KILLED)` directly.

## 5. `cmd.run.result`

stdout/stderr content is inlined when each stream is at most 1k. When a stream
is larger than 1k, only file metadata is included. In this document, 1k is fixed
to 1024 bytes and stdout and stderr are evaluated independently.

```json
{
  "kind": "cmd.run.result",
  "prev_seq": 12345,
  "ts_ms": 1710000001234,
  "status": "SUCCESS",
  "exit_code": 0,
  "timeout_ms": 300000,
  "started_at_ms": 1710000000100,
  "finished_at_ms": 1710000001200,
  "stdout": {"file": "2001/12345.stdout", "bytes": 6, "content_encoding": "utf-8", "content": "hello\n"},
  "stderr": {"file": "2001/12345.stderr", "bytes": 0, "content_encoding": "utf-8", "content": ""}
}
```

Rules:

- OpenEvent `principal` is the cmd-worker principal.
- OpenEvent `recipients` is the sender principal of the original
  `cmd.run.request`.
- `prev_seq` is required and must equal the OpenEvent seq of the corresponding
  run request.
- The worker creates output subdirectories by OpenEvent top-level `channel_id`.
  Output files from different channels must be separated.
- `stdout.file` is fixed to `<channel_id>/<prev_seq>.stdout`; `stderr.file` is
  fixed to `<channel_id>/<prev_seq>.stderr`. `file` is relative to `output_dir`.
- stdout and stderr are evaluated independently with a 1024-byte threshold:
  - When `bytes <= 1024`, `content` is required and contains the full content of
    that output file.
  - When `bytes > 1024`, `content` must be absent and only file metadata is
    written.
- The actual output root is configured by worker `output_dir`.
- `timeout_ms` is the effective timeout used for this run request. It is
  required except when `status="REJECTED"` and the effective timeout cannot be
  determined.
- `finished_at_ms` is the time when the worker writes the final run result. It
  is required for all `cmd.run.result` messages.
- `started_at_ms` appears only after the command process actually starts.
- `exit_code` appears only when `status="SUCCESS"` or `status="FAILED"`.
- `error_message` is required when `status="REJECTED"`,
  `status="START_FAILED"`, or `status="RECOVERY_FAILED"`.
- When `status="REJECTED"`, the worker does not start the command and writes
  empty output objects for stdout/stderr.
- When the request expires before startup, is killed while pending, is invalid,
  or fails to start, stdout/stderr are empty output objects.
- Startup failure includes failure to create the channel output directory,
  create or open stdout/stderr files, enter `workdir`, or start the shell
  process.
- When `status="RECOVERY_FAILED"`, if original stdout/stderr files exist, the
  worker writes output objects based on those files; otherwise it writes empty
  output objects.
- Empty output objects only mean the output is empty in this result. They do not
  require the corresponding stdout/stderr file to exist.

### 5.1 stdout/stderr Output Object

stdout and stderr use the same structure. `file` and `bytes` are required.
Whether `content` appears is determined by that stream's own `bytes`. When
`content` appears, `content_encoding` must also appear.

Short output example, `bytes <= 1024`:

```json
{
  "file": "2001/12345.stdout",
  "bytes": 6,
  "content_encoding": "utf-8",
  "content": "hello\n"
}
```

Long output example, `bytes > 1024`:

```json
{
  "file": "2001/12345.stdout",
  "bytes": 2048
}
```

Empty output example:

```json
{
  "file": "2001/12345.stdout",
  "bytes": 0,
  "content_encoding": "utf-8",
  "content": ""
}
```

Rules:

- `file` is a path relative to `output_dir`; it does not include `output_dir`.
- `bytes` is the byte count of that output file.
- `content` is the full content for short output. Long output must not include
  `content`.
- `content_encoding` appears only when `content` appears. It is either `utf-8`
  or `base64`.
- If output bytes can be decoded as UTF-8, `content_encoding="utf-8"` and
  `content` is the decoded string.
- If output bytes cannot be decoded as UTF-8, `content_encoding="base64"` and
  `content` is the base64 string of the raw bytes.
- Empty output objects must use `bytes=0`, `content_encoding="utf-8"`, and
  `content=""`.
- Empty output objects do not imply that the file pointed to by `file` exists.
  Later `cmd.output.read.request` still returns based on whether the actual file
  exists.
- stdout and stderr are evaluated independently. For example, if stdout is 100
  bytes and stderr is 4096 bytes, stdout must include `content` while stderr
  only includes file metadata.

`status` values:

| Status | Meaning |
| --- | --- |
| `SUCCESS` | Process exit code is 0. |
| `FAILED` | Process exit code is non-zero. |
| `TIMEOUT` | Worker killed the process due to timeout, or found before startup that the request had already expired. |
| `KILLED` | Worker killed the process due to a kill request, or removed a not-yet-started task from the pending queue. |
| `START_FAILED` | Preparation failed before process startup or the process could not start, including output file create/open failure, working directory enter failure, or shell startup failure. |
| `RECOVERY_FAILED` | On startup scan, the worker found a historical run request with `cmd.run.accepted` but no final result and completed it with a failed result. |
| `REJECTED` | Run request payload is invalid or exceeds protocol/configuration limits; the worker did not execute the command. |

## 6. `cmd.kill.request`

```json
{
  "kind": "cmd.kill.request",
  "ts_ms": 1710000000500,
  "target_seq": 12345
}
```

Rules:

- `target_seq` is required and must be a positive integer. It is expected to
  point to the OpenEvent seq of a `cmd.run.request`.
- The worker handles only tasks running or pending in the current worker
  process.
- If the target task is still pending and has not started, the worker removes it
  from the pending queue directly.
- Any member of the same `cmd.v1` channel can kill running or pending tasks in
  that channel. The kill requester does not have to be the original submitter.
- Kill requests only affect tasks in the channel where the request is published.
  If `target_seq` points to a run request in another channel, the worker does
  not operate across channels and returns `NOT_FOUND`.

## 7. `cmd.kill.result`

```json
{
  "kind": "cmd.kill.result",
  "prev_seq": 12346,
  "target_seq": 12345,
  "ts_ms": 1710000000520,
  "status": "KILL_SENT"
}
```

Rules:

- OpenEvent `principal` is the cmd-worker principal.
- OpenEvent `recipients` is the sender principal of the original
  `cmd.kill.request`.
- `prev_seq` is required and must equal the OpenEvent seq of the corresponding
  kill request.
- `target_seq` must be echoed when a valid value can be parsed from the original
  request. If the original request's `target_seq` is invalid or missing, it may
  be omitted when `status="REJECTED"`.
- `error_message` is required when `status="REJECTED"`.

`status` values:

| Status | Meaning |
| --- | --- |
| `KILL_SENT` | Target task is still running and the worker sent a kill signal. |
| `REMOVED_PENDING` | Target task had not started and the worker removed it from the pending queue. |
| `ALREADY_FINISHED` | Target task had already finished and the worker did not send a kill signal; if its run result had not been written, the worker first writes the final run result. |
| `NOT_FOUND` | Worker did not find the target task. |
| `REJECTED` | Kill request payload is invalid or exceeds protocol/configuration limits; the worker did not send a kill signal. |

When the target task is still running, the worker writes
`cmd.kill.result(status=KILL_SENT)`, then the target task writes
`cmd.run.result(status=KILLED)`. When the target task has not started and is
still in the pending queue, the worker writes
`cmd.kill.result(status=REMOVED_PENDING)` and also writes
`cmd.run.result(status=KILLED)` for the target run request. If the worker handles
a kill request and finds that the target process has already exited but its
completion event has not been processed, it must first write
`cmd.run.result(status=SUCCESS)` or `cmd.run.result(status=FAILED)` according to
the target process exit code, then write
`cmd.kill.result(status=ALREADY_FINISHED)`.

## 8. `cmd.output.read.request`

Callers can read a segment of a command output file through a message. The
request only reads `<target_seq>.stdout` or `<target_seq>.stderr` under the
worker-local `output_dir/<channel_id>/`.

```json
{
  "kind": "cmd.output.read.request",
  "request_id": "cmd:agent:s1:wal:100:1",
  "ts_ms": 1710000002000,
  "target_seq": 12345,
  "stream": "stdout",
  "offset": 0,
  "nbytes": 1024
}
```

Rules:

- OpenEvent `principal` is the querying principal.
- OpenEvent `recipients` must be empty.
- `request_id` is required. It is a caller-generated idempotency identifier and
  may contain only letters, digits, dots, underscores, colons, and hyphens.
- `target_seq` is required and must be the OpenEvent seq of the target
  `cmd.run.request`.
- `stream` is required and must be either `stdout` or `stderr`.
- `offset` is required and must be a non-negative integer. It is the byte offset
  in the output file where reading begins.
- `nbytes` is required and must be a positive integer. It is the maximum number
  of bytes the caller wants to read in this request.
- The worker may return `bytes` smaller than `nbytes`. If the read content,
  after JSON and any required base64 encoding, would make
  `cmd.output.read.result` exceed the OpenEvent deployment payload limit, the
  worker must reduce the actual number of bytes read so the result payload can
  be written to OpenEvent. Callers continue reading with `offset + bytes`.
- `cmd.output.read.request` does not change the target task state; it only reads
  existing output file content.
- Any member of the same `cmd.v1` channel can read command output in that
  channel.
- Partial output reads only operate on the output directory for the request
  channel. If `target_seq` points to a run request in another channel, the
  worker does not look across channels and returns `NOT_FOUND`.
- The field is named `target_seq` rather than `seq` to avoid confusion with
  OpenEvent top-level message `seq`.

## 9. `cmd.output.read.result`

The worker writes content back for a partial read request:

```json
{
  "kind": "cmd.output.read.result",
  "prev_seq": 12347,
  "target_seq": 12345,
  "ts_ms": 1710000002010,
  "status": "SUCCESS",
  "stream": "stdout",
  "offset": 0,
  "nbytes": 1024,
  "bytes": 128,
  "eof": false,
  "content_encoding": "utf-8",
  "content": "..."
}
```

Rules:

- OpenEvent `principal` is the cmd-worker principal.
- OpenEvent `recipients` is the sender principal of the original
  `cmd.output.read.request`.
- `prev_seq` is required and must equal the OpenEvent seq of the corresponding
  output read request.
- `target_seq`, `stream`, `offset`, and `nbytes` must be echoed when valid
  values can be parsed from the original request. If a field is invalid or
  missing in the original request, it may be omitted when `status="REJECTED"`.
- `status` is required and describes the processing result for this partial
  read request.
- `bytes` is the byte count actually written to `content`; it is `0` when no
  file content was read.
- `content` is required and contains the content read by this request. If
  `offset` is already at the file size observed at read start, `content` is an
  empty string, `bytes=0`, and `eof=true`.
- `content_encoding` is required and must be `utf-8` or `base64`.
- If the bytes read can be decoded as UTF-8, `content_encoding="utf-8"` and
  `content` is the decoded string.
- If the bytes read cannot be decoded as UTF-8, `content_encoding="base64"` and
  `content` is the base64 string of the raw bytes.
- If the worker reduces the read size to keep the result payload under the
  OpenEvent deployment limit, `bytes` is the actual returned byte count. As long
  as `offset + bytes` has not reached the file size observed at read start,
  `eof` must be `false`.
- `eof` indicates whether this read reached the file size observed at read
  start. The worker opens the file and reads its size first. If
  `offset + bytes` is greater than or equal to that size, `eof=true`. When the
  target task is still running, `eof=true` does not mean the task will not write
  more data later.
- If the target output file does not exist, the worker writes
  `status="NOT_FOUND"`, `content=""`, `bytes=0`, `content_encoding="utf-8"`,
  and `eof=true`.
- If the target output file exists but the worker cannot stat, open, or read it,
  the worker writes `status="READ_FAILED"`, `content=""`, `bytes=0`,
  `content_encoding="utf-8"`, `eof=false`, and `error_message`.
- If the output read request payload is invalid or exceeds protocol/config
  limits, the worker writes `status="REJECTED"`, `content=""`, `bytes=0`,
  `content_encoding="utf-8"`, `eof=true`, and `error_message`, and does not read
  the output file.

Successful result when the target file exists:

```json
{
  "kind": "cmd.output.read.result",
  "prev_seq": 12347,
  "target_seq": 12345,
  "ts_ms": 1710000002010,
  "status": "SUCCESS",
  "stream": "stdout",
  "offset": 0,
  "nbytes": 1024,
  "bytes": 6,
  "eof": true,
  "content_encoding": "utf-8",
  "content": "hello\n"
}
```

Result when the target file does not exist:

```json
{
  "kind": "cmd.output.read.result",
  "prev_seq": 12347,
  "target_seq": 12345,
  "ts_ms": 1710000002010,
  "status": "NOT_FOUND",
  "stream": "stdout",
  "offset": 0,
  "nbytes": 1024,
  "bytes": 0,
  "eof": true,
  "content_encoding": "utf-8",
  "content": ""
}
```

`status` values:

| Status | Meaning |
| --- | --- |
| `SUCCESS` | Read succeeded and `content` is the returned content. |
| `NOT_FOUND` | Target output file does not exist. |
| `READ_FAILED` | Target output file exists but the worker cannot stat, open, or read it. |
| `REJECTED` | Output read request payload is invalid or exceeds protocol/configuration limits; the worker did not read the output file. |

## 10. Versioning Policy

- `cmd.v1` does not support old-worker compatibility for new fields. When
  protocol fields change, documentation, SDK, and worker are updated together.
- Breaking changes must use a new protocol, such as `cmd.v2`.

## 11. Confirmed Protocol Decisions

- Kill requests and output partial-read requests use `target_seq` as the fixed
  target task field.
- `target_seq` is the OpenEvent seq of the target `cmd.run.request`; it avoids
  confusion with OpenEvent top-level message `seq`.
