# Cmd Protocol SDK Usage

[中文版](SDK_USAGE_cn.md)

See the [protocol](CMD_PROTOCOL.md) for message formats.

## Public Interface

Use a Cmd channel and credentials from your deployment. The command below writes six bytes to stdout.

```python
import time

from openevent.sdk import OpenEventClient
from openevent.cmd_sdk import (
    CmdRunRequestInput,
    CmdKillRequestInput,
    CmdOutputReadRequestInput,
    create_client,
    parse_message,
)

openevent_client = OpenEventClient("127.0.0.1:9527")
client = create_client(openevent_client)
principal, token, channel_id = 1001, "token-1001", 2001

run_uuid = openevent_client.get_uuid()
run_request = CmdRunRequestInput(
    command="echo hello",
    timeout_ms=300000,
    ts_ms=time.time_ns() // 1_000_000,
)
attachments = []
run_seq = client.publish_run_request(
    principal=principal,
    token=token,
    channel_id=channel_id,
    req=run_request,
    object_keys=attachments,
    uuid=run_uuid,
)
```

Subscribe from the sequence after the run request to receive replies that have already arrived and those that follow.
Once the matching run returns `SUCCESS`, request the six bytes and print the reply.
Skip [OpenEvent system messages](CONFIGURATION.md#channel-requirements) before parsing the payload.

```python
replies = openevent_client.subscribe(
    principal, token, from_seq=run_seq + 1, channels=[channel_id], only_my_recipient=True,
)
read_seq = None
try:
    for item in replies:
        if item.message.principal == 0:
            continue
        payload = parse_message(item.message).payload
        if payload.kind == "cmd.run.accepted" and payload.prev_seq == run_seq:
            print("accepted", run_seq)
        elif payload.kind == "cmd.run.result" and payload.prev_seq == run_seq:
            if payload.status != "SUCCESS":
                print(payload.status, payload.error_message)
                break
            read_seq = client.publish_output_read_request(
                principal, token, channel_id,
                CmdOutputReadRequestInput(target_seq=run_seq, stream="stdout", offset=0, nbytes=6),
            )
        elif payload.kind == "cmd.output.read.result" and payload.prev_seq == read_seq:
            if payload.status == "SUCCESS":
                print(payload.content, payload.content_encoding)
            else:
                print(payload.status, payload.error_message)
            break
finally:
    replies.cancel()
```

The run result itself also carries `stdout.file` and `stdout.bytes`. Short outputs include `stdout.content` and `stdout.content_encoding`.

To try cancellation separately, submit a long-running command and cancel that task using its own sequence number.

```python
long_run_seq = client.publish_run_request(
    principal, token, channel_id, CmdRunRequestInput(command="sleep 60"),
)
kill_seq = client.publish_kill_request(
    principal, token, channel_id, CmdKillRequestInput(target_seq=long_run_seq),
)
```

## Integration Notes

- Publish methods return the integer message seq and use OpenEvent
  `PublishAutoSeq`. Request methods set empty recipients automatically.
- `publish_run_request(..., object_keys=())` accepts an iterable of OpenEvent SDK
  `ObjectKey` values and writes them into the message envelope. For commands that need
  an attachment, add an `openevent.sdk.proto.openevent_pb2.ObjectKey` to `attachments`
  using the `object_id` and `object_token` returned by `write_object`. See the [run request protocol](CMD_PROTOCOL.md#3-cmdrunrequest)
  for how child processes receive credentials.
- Every publish method accepts the optional keyword argument `uuid`. If omitted,
  the installed OpenEvent SDK allocates a UUID; otherwise the supplied value is
  used. For a run, keep the UUID, request, and attachments until you have the
  `run_seq`, as in the example. After a timeout or a lost response, retry the original
  request with the same UUID. On `ALREADY_EXISTS`, call
  `openevent_client.get_seq_by_uuid(run_uuid)` to obtain the committed task sequence.
  Retrying with a new UUID can create a second run request. These helpers do not retry automatically.
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
