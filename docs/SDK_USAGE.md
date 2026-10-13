# Cmd Protocol SDK Usage

[中文版](SDK_USAGE_cn.md)

See the [protocol](CMD_PROTOCOL.md) for message formats.

## Public Interface

```python
from openevent.sdk import OpenEventClient
from openevent.sdk.proto import openevent_pb2
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
    object_keys=[openevent_pb2.ObjectKey(object_id=123, object_token="object-token-123")],
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
        nbytes=6,
    ),
)
```

Skip [OpenEvent system messages](CONFIGURATION.md#channel-requirements), then
parse Cmd messages with the module-level helper:

```python
from openevent.cmd_sdk import parse_message

if message.principal != 0:
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

    if parsed.payload.kind in {"cmd.run.result", "cmd.output.read.result"}:
        if parsed.payload.error_message is not None:
            print(parsed.payload.error_message)
```

## Integration Notes

- Publish methods return the integer message seq and use OpenEvent
  `PublishAutoSeq`. Request methods set empty recipients automatically.
- `publish_run_request(..., object_keys=())` accepts an iterable of OpenEvent SDK
  `ObjectKey` values and writes them into the message envelope. Omitting it sends
  no attachments. See the [run request protocol](CMD_PROTOCOL.md#3-cmdrunrequest)
  for how child processes receive credentials.
- Every publish method accepts the optional keyword argument `uuid`. If omitted,
  the installed OpenEvent SDK allocates a UUID; otherwise the supplied value is
  used. These helpers do not retry automatically. UUID reuse and message
  consistency follow the OpenEvent SDK API contract.
- `parse_message(message)` parses only Cmd messages and returns `ParsedMessage`
  with top-level seq, channel_id, principal, recipients, an `object_keys` tuple,
  and a validated payload model. Invalid payloads raise `CmdSDKError`, exposing
  `code` and `message`; OpenEvent RPC errors propagate unchanged.
- Read `content_encoding` alongside output content. The complete requested byte
  range must be returnable; see the [exact-read rules](CMD_PROTOCOL.md#8-cmdoutputreadrequest).

## Worker Publish Interface

`request_principal` is the original request sender. Publishing automatically sets
`recipients=[request_principal]`.

```text
client.publish_run_accepted(principal, token, channel_id, request_principal, prev_seq, *, uuid=None)
client.publish_run_result(principal, token, channel_id, request_principal, result_payload, *, uuid=None)
client.publish_kill_result(principal, token, channel_id, request_principal, result_payload, *, uuid=None)
client.publish_output_read_result(principal, token, channel_id, request_principal, result_payload, *, uuid=None)
```

The three `result_payload` arguments use `CmdRunResultInput`, `CmdKillResultInput`,
and `CmdOutputReadResultInput`, respectively.
Construction requires explicit values for `CmdRunResultInput.timeout_ms`,
`CmdKillResultInput.target_seq`, and the `target_seq`, `stream`, `offset`, and
`nbytes` parameters of `CmdOutputReadResultInput`; none may be omitted. See the
[run result](CMD_PROTOCOL.md#5-cmdrunresult), [kill result](CMD_PROTOCOL.md#7-cmdkillresult),
and [output-read result](CMD_PROTOCOL.md#9-cmdoutputreadresult) protocols for field
meanings and status requirements.
