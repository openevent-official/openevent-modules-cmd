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

- Publish methods return the integer message seq and use OpenEvent
  `PublishAutoSeq`. Request methods set empty recipients automatically.
- Every publish method accepts the optional keyword argument `uuid`. If omitted,
  the installed OpenEvent SDK allocates a UUID; otherwise the supplied value is
  used. These helpers do not retry automatically. UUID reuse and message
  consistency follow the OpenEvent SDK API contract.
- `parse_message(message)` returns `ParsedMessage` with top-level seq, channel_id,
  principal, recipients, and a validated payload model. Invalid payloads raise
  `CmdSDKError`, exposing `code` and `message`; OpenEvent RPC errors propagate
  unchanged.
- Output content, encoding, states, and field rules are defined in the
  [protocol](CMD_PROTOCOL.md). Read `content_encoding` alongside content.
- The worker uses `Fetch(only_my_recipient=false, channels=channel_ids)` to read
  requests and results from configured channels.

## Worker Publish Interface

These methods are intended for workers. `request_principal` is the original
request sender. The helper sets `recipients=[request_principal]`; there is no
argument named `recipients`.

```text
client.publish_run_accepted(principal, token, channel_id, request_principal, prev_seq, *, uuid=None)
client.publish_run_result(principal, token, channel_id, request_principal, result_payload, *, uuid=None)
client.publish_kill_result(principal, token, channel_id, request_principal, result_payload, *, uuid=None)
client.publish_output_read_result(principal, token, channel_id, request_principal, result_payload, *, uuid=None)
```

These are method signatures. The three `result_payload` arguments use
`CmdRunResultInput`, `CmdKillResultInput`, and `CmdOutputReadResultInput`,
respectively. Like request methods, they return an integer seq.
