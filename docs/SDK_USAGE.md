# Cmd Protocol SDK Usage

[中文版](SDK_USAGE_cn.md)

> Version: v0.1
> Scope: Python callers that publish, parse, and handle `cmd.v1` messages.

See [CMD_PROTOCOL.md](CMD_PROTOCOL.md) for the protocol specification. This
document describes the public Python SDK interface.

## Public Interface

```python
from openevent.sdk import OpenEventClient
from openevent.cmd_sdk import (
    CmdRunRequestInput,
    CmdKillRequestInput,
    CmdOutputReadRequestInput,
    create_client,
)

openevent_client = OpenEventClient("127.0.0.1:9527")
client = create_client(openevent_client)

run_seq = client.publish_run_request(
    principal=1001,
    token="token-1001",
    channel_id=2001,
    req=CmdRunRequestInput(
        command="echo hello",
        workdir="/absolute/path/to/workdir",
        timeout_ms=300000,
    ),
)

kill_seq = client.publish_kill_request(
    principal=1001,
    token="token-1001",
    channel_id=2001,
    req=CmdKillRequestInput(target_seq=run_seq),
)

read_seq = client.publish_output_read_request(
    principal=1001,
    token="token-1001",
    channel_id=2001,
    req=CmdOutputReadRequestInput(
        target_seq=run_seq,
        stream="stdout",
        offset=0,
        nbytes=1024,
    ),
)
```

Parse messages with the module-level helper:

```python
from openevent.cmd_sdk import parse_message

parsed = parse_message(message)
if parsed.payload.kind == "cmd.run.accepted":
    print(parsed.payload.prev_seq)

if parsed.payload.kind == "cmd.run.result":
    if parsed.payload.stdout.content is not None:
        print(parsed.payload.stdout.content, parsed.payload.stdout.content_encoding)
    else:
        print(parsed.payload.stdout.file, parsed.payload.stdout.bytes)

if parsed.payload.kind == "cmd.output.read.result":
    print(parsed.payload.content, parsed.payload.content_encoding)

if parsed.payload.kind in {"cmd.run.result", "cmd.kill.result", "cmd.output.read.result"}:
    if parsed.payload.error_message is not None:
        print(parsed.payload.error_message)
```

## Integration Notes

- Publish helpers use OpenEvent `PublishAutoSeq`.
- `cmd.run.request`, `cmd.kill.request`, and `cmd.output.read.request` are
  published with empty `recipients`.
- The cmd worker uses OpenEvent `Fetch` with `only_my_recipient=false` and
  passes configured `channel_ids` through `channels`; otherwise it would miss
  request messages with empty `recipients` or process channels outside its scope.
- `CmdRunRequestInput.workdir`, when provided, must be an absolute path.
- `cmd.run.accepted` and result publish helpers are intended for `cmd-worker`;
  callers must pass recipients explicitly.
- stdout/stderr objects in `cmd.run.result` include `content` when
  `bytes <= 1024`; when `bytes > 1024`, they include only file metadata.
- When `content` is present, callers must also check `content_encoding`.
  `utf-8` means the content is a UTF-8 string; `base64` means it is the base64
  representation of the raw bytes.
- `cmd.output.read.result.content` always contains the bytes returned by that
  partial read. When the target file does not exist, it is an empty string with
  `content_encoding="utf-8"`.
- Any member of a channel can publish `cmd.output.read.request` to read command
  output in that channel.
- The SDK only validates that `cmd.output.read.request.nbytes` is a positive
  integer. The worker controls the actual returned size based on the OpenEvent
  payload limit.
- SDK code must be prepared to read `error_message` from result types.
  `REJECTED`, `START_FAILED`, `READ_FAILED`, and `RECOVERY_FAILED` use this
  field to explain the reason.
- Duplicate accepted/result checks should use `kind + prev_seq` within the same
  channel.
- The SDK validates payload structure. Channel ownership, command safety, and
  permission boundaries are deployment responsibilities.
