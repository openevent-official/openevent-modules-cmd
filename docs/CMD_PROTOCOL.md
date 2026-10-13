# Cmd Protocol cmd.v3

[中文版](CMD_PROTOCOL_cn.md)

## 1. Channel

Cmd channels must set `protocol="cmd.v3"` and must not use public visibility.
Both caller and worker principals must be channel members. Only one active
worker may consume a channel at a time.

`description` is optional plain text and may be empty; the worker does not parse it.

## 2. Common Rules

The following rules apply only to Cmd messages. See the
[runtime notes](CONFIGURATION.md#channel-requirements) for OpenEvent system messages.
All Cmd payloads are UTF-8 JSON objects. Unknown fields are invalid. Common fields:

- `kind`: required; one of the message types in this document's section headings.
- `ts_ms`: required; a Unix millisecond timestamp set by the payload producer.

Identity and routing use OpenEvent top-level fields. Top-level
`EventMessage.ts_ms` is the Unix millisecond timestamp generated when the server
receives the write request, distinct from the payload creation time.

For all request messages, OpenEvent `principal` is the caller principal and
`recipients` must be empty.

For all accepted/result messages, OpenEvent `principal` is the worker principal
and `recipients` is the corresponding request sender's principal. Payload
`prev_seq` is required and equals that request's OpenEvent `seq`.

Output `content_encoding` is `utf-8` or `base64`: if the raw bytes decode as
UTF-8, `content` is the decoded string; otherwise it is the base64 string of those
bytes. Both fields must appear together. Empty output uses `bytes=0`,
`content_encoding="utf-8"`, and `content=""`.

If a non-system message's payload cannot be decoded as a UTF-8 JSON object, or its
message type or fields fail validation, the worker reports an error and exits.
It neither skips the message nor writes a Cmd result for it. This applies to
requests, accepted, and results during both normal consumption and startup
recovery. See [runtime behavior](CONFIGURATION.md#recovery-and-exit) for exit and cleanup.

The `error_message` for `START_FAILED`, `RECOVERY_FAILED`, and `READ_FAILED` uses
at most 512 UTF-8 bytes, truncated at complete character boundaries.

`max_payload_bytes` is the OpenEvent deployment's message payload limit; the
reference implementation defaults to 16 MiB. All size limits in this document
use the actual deployment value. See [configuration](CONFIGURATION.md#fields)
for the worker's supported minimum. All accepted/result payloads are checked
after encoding and before publication. A partial read whose complete successful
result cannot fit returns `READ_FAILED` under sections 8 and 9, without reducing
the return size. Other results that cannot fit cause the worker to fail and exit.

## 3. `cmd.run.request`

```json
{
  "kind": "cmd.run.request",
  "ts_ms": 1710000000000,
  "command": "echo hello",
  "workdir": "/absolute/path/to/workdir",
  "timeout_ms": 300000
}
```

- The message's OpenEvent `seq` is the command task ID.
- `command` is required: a non-empty string containing a complete Linux command line. The worker
  executes it through `/bin/sh -c <command>` by default. See
  [Task Process Lifecycle](CONFIGURATION.md#task-process-lifecycle) for the scope
  of process management. Separate `env` and `stdin` fields are not supported.
- `workdir` is optional: a non-empty absolute path string. If absent, the worker
  uses its current working directory. A missing path, a non-directory, denied
  access, or a path that prevents shell startup produces `START_FAILED` with
  `error_message`; it is not an invalid payload.
- `timeout_ms` is optional: an integer in `1..18446744073709551615` (`2**64-1`).
  If absent, the current default at request handling time applies.
  [Startup recovery](CONFIGURATION.md#recovery-and-exit) also uses current
  configuration, including for `RECOVERY_FAILED`.
- The deadline is this message's top-level `EventMessage.ts_ms` plus the effective
  `timeout_ms`, never the payload's `ts_ms`.

The caller may attach the object read credentials needed by the command in this
message's top-level OpenEvent `object_keys`; this is not a payload field. The
worker passes these credentials into this command's environment, so the command
does not need to query the caller for them. The rules are:

- The worker inherits its own process environment and sets `OPENEVENT_OBJECT_KEYS`
  for this shell.
- Its value is a JSON array in request order. Each entry contains an integer
  `object_id` and a string `object_token`, preserving the complete ObjectKey.
  No attachments means `[]`, overriding any inherited value in the worker's
  environment. Credentials are neither shared nor accumulated across tasks.
- The variable is inherited by the command and its children and can be read
  repeatedly. The worker does not insert credentials into `command` or print them.
- The deployment environment supplies the object service address. Cmd does not
  download objects or provide an attachment reader.
- Credentials and other process environment values are subject to the operating
  system's argument and environment size limits. A resulting startup failure
  produces `START_FAILED`. Other requests and accepted/result messages do not
  forward task attachment credentials.

For example, two attachments produce this environment variable value:

```json
[{"object_id":123,"object_token":"object-token-123"},{"object_id":456,"object_token":"object-token-456"}]
```

## 4. `cmd.run.accepted`

Before starting a valid, non-expired run request, the worker must confirm that
accepted was written. It then writes a final `cmd.run.result`; startup recovery
completes unresolved requests with a failed result.

```json
{
  "kind": "cmd.run.accepted",
  "prev_seq": 12345,
  "ts_ms": 1710000000050
}
```

- After accepted is confirmed, the worker checks the deadline again. If it
  expired during publication, no process starts and the result is `TIMEOUT`.
  Accepted therefore does not guarantee that the command has started.
- Requests that expire before obtaining an execution slot and requests killed
  while pending receive `TIMEOUT` and `KILLED` run results respectively, without accepted.

## 5. `cmd.run.result`

```json
{
  "kind": "cmd.run.result",
  "prev_seq": 12345,
  "ts_ms": 1710000001234,
  "status": "SUCCESS",
  "exit_code": 0,
  "timeout_ms": 300000,
  "started_at_ms": 1710000000100,
  "stdout": {"file": "2001/12345.stdout", "bytes": 6, "content_encoding": "utf-8", "content": "hello\n"},
  "stderr": {"file": "2001/12345.stderr", "bytes": 0, "content_encoding": "utf-8", "content": ""}
}
```

- `timeout_ms` is required and is the effective timeout defined in section 3.
  `RECOVERY_FAILED` uses the explicit request value or the current recovery default.
- The common `ts_ms` field is this result's creation time, not the actual command
  exit time. `started_at_ms` is the actual process start time and appears only
  after the process starts.
- `exit_code` is the shell exit code and appears only for `SUCCESS` or `FAILED`.
- `START_FAILED` and `RECOVERY_FAILED` require `error_message`.
- Expiry before startup, a kill while pending, or startup
  failure produces empty stdout/stderr objects. `RECOVERY_FAILED` uses existing
  output files, or empty output objects if the files are missing.

`status` values:

| Status | Meaning |
| --- | --- |
| `SUCCESS` | No worker timeout or kill-request termination; shell exit code is 0. |
| `FAILED` | No worker timeout or kill-request termination; shell exit code is non-zero. |
| `TIMEOUT` | Process killed on timeout, or request expired before startup. |
| `KILLED` | Process killed by a kill request, or pending task removed from the queue. |
| `START_FAILED` | Startup failed, including output subdirectory/file creation, file opening, closing the worker's output file handles, working directory access, or shell startup. |
| `RECOVERY_FAILED` | Startup scan found a historical run request with accepted but no final result and completed it with a failed result. |

### 5.1 stdout/stderr Output Object

stdout and stderr independently follow the same rules:

- `file` is required: a path relative to the worker's configured `output_dir`,
  fixed to `<channel_id>/<prev_seq>.stdout` or `<channel_id>/<prev_seq>.stderr`.
  `channel_id` comes from the OpenEvent top-level field.
- `bytes` is required. Output at most 1024 bytes must include `content` and
  `content_encoding` with the full content within this read's range. Larger
  output includes only file metadata and must omit `content`.
- After opening the file, the worker obtains its initial size through the same
  handle. Short output reads at most that range, excluding later appends and
  including only bytes actually read if the file shrinks; `bytes` is the actual
  count read. For long output, `bytes` is the initial file size.
- Empty output objects do not guarantee that `file` exists. Subsequent partial
  reads return according to actual file existence.

Long output example:

```json
{
  "file": "2001/12345.stdout",
  "bytes": 2048
}
```

When building a run result, a missing file produces an empty output object.
Other failures to open, obtain the size of, or read the file cause the worker to
fail and exit without publishing this run result. The next startup handles
unresolved requests under the [recovery rules](CONFIGURATION.md#recovery-and-exit);
a read failure during recovery still causes exit. Partial-read errors follow
section 9.

## 6. `cmd.kill.request`

```json
{
  "kind": "cmd.kill.request",
  "ts_ms": 1710000000500,
  "target_seq": 12345
}
```

- `target_seq` is required: a positive integer expected to identify the OpenEvent
  `seq` of a `cmd.run.request`.
- The worker only terminates running or pending tasks in its current process;
  pending tasks are removed directly from the queue.
- Any channel member can kill tasks in that channel, without being the original
  submitter. A `target_seq` in another channel returns `NOT_RUNNING`; the worker
  does not operate across channels.

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

`target_seq` is required and must echo the request's value. This message has no
`error_message` field.

`status` values:

| Status | Meaning |
| --- | --- |
| `KILL_SENT` | Target shell is still running; a kill signal was sent to its task process group. |
| `REMOVED_PENDING` | Target had not started and was removed from the queue. |
| `NOT_RUNNING` | No running or pending target can be terminated in this channel. Completed and nonexistent tasks are not distinguished. |

If the kill triggers termination first, the run result is `KILLED`; if a timeout
already triggered, it remains `TIMEOUT`. When removing a pending task, the worker
confirms the `KILLED` run result before writing `REMOVED_PENDING`. A crash between
these results leaves the run request closed after restart; the unfinished kill
request returns `NOT_RUNNING`.

If the shell has exited but cleanup is unfinished, the worker completes cleanup
and reaping under [Task Process Lifecycle](CONFIGURATION.md#task-process-lifecycle),
writes the run result, then writes `NOT_RUNNING`. The run result preserves an
established `TIMEOUT`/`KILLED`; otherwise the shell exit code selects
`SUCCESS`/`FAILED`. Cleanup of remaining group members does not change it to `KILLED`.

## 8. `cmd.output.read.request`

Reads the exact requested byte range from the worker-local
`output_dir/<channel_id>/<target_seq>.stdout` or `.stderr` without changing task state.

```json
{
  "kind": "cmd.output.read.request",
  "ts_ms": 1710000002000,
  "target_seq": 12345,
  "stream": "stdout",
  "offset": 0,
  "nbytes": 6
}
```

- `target_seq` is required: the target `cmd.run.request`'s OpenEvent `seq`.
- `stream` is required: `stdout` or `stderr`.
- `offset` is required: a non-negative integer byte offset at which reading starts.
- `nbytes` is required: a positive integer specifying the exact byte count to
  read. The requested range is `[offset, offset + nbytes)`.
- The worker must return the complete range without reducing the read size or
  returning partial content. If the range exceeds the initial file size obtained
  after opening, fewer than `nbytes` bytes are actually read, or the complete result
  exceeds the deployment payload limit after JSON and any required base64 encoding,
  it returns `READ_FAILED` with an explanation. An empty file or an `offset` at or
  beyond the file end also returns `READ_FAILED`.
- Any channel member can read that channel's output. A `target_seq` in another
  channel returns `NOT_FOUND`; the worker does not look across channels.

## 9. `cmd.output.read.result`

```json
{
  "kind": "cmd.output.read.result",
  "prev_seq": 12347,
  "target_seq": 12345,
  "ts_ms": 1710000002010,
  "status": "SUCCESS",
  "stream": "stdout",
  "offset": 0,
  "nbytes": 6,
  "bytes": 6,
  "eof": true,
  "content_encoding": "utf-8",
  "content": "hello\n"
}
```

- `target_seq`, `stream`, `offset`, and `nbytes` are required and must echo the
  request's values.
- `status`, `content`, and `content_encoding` are required. On success, `bytes`
  must equal the requested `nbytes`, and decoding `content` must produce all raw
  bytes in the requested range.
- After opening the file, the worker obtains its initial size. On success, `eof`
  is `offset + nbytes == initial file size`. `eof=true` does not guarantee a running
  task will not append more output.

`status` values and failure fields:

| Status | Meaning | `eof` | `error_message` |
| --- | --- | --- | --- |
| `SUCCESS` | Read succeeded. | As defined above | — |
| `NOT_FOUND` | Target output file does not exist. | `true` | — |
| `READ_FAILED` | File exists but cannot be statted, opened, or read, or the exact range or message size requirements in section 8 cannot be met. | `false` | Required |

The last two statuses use `content=""`, `bytes=0`, and `content_encoding="utf-8"`.

## 10. Versioning Policy

This package implements `cmd.v3`. It requires channels and callers using this protocol.
Breaking changes must use a new protocol, such as `cmd.v4`.
