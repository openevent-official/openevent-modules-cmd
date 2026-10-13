# Cmd 协议 SDK 用法

[English version](SDK_USAGE.md)

消息格式见[协议](CMD_PROTOCOL_cn.md)。

## 公开接口

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

先区分[OpenEvent 系统消息](CONFIGURATION_cn.md#channel-要求)，再解析 Cmd 业务消息：

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

## 集成说明

- 发布方法返回消息的整数 seq，使用 OpenEvent `PublishAutoSeq`；请求方法自动使用空 recipients。
- `publish_run_request(..., object_keys=())` 接收可迭代的 OpenEvent SDK `ObjectKey`，写入消息顶层。
  省略时不带附件；子进程如何取得凭据见[运行请求协议](CMD_PROTOCOL_cn.md#3-cmdrunrequest)。
- 所有发布方法支持可选关键字参数 `uuid`。省略时从已安装的 OpenEvent SDK 分配 UUID；指定时使用传入值。
  本辅助层不自动重试。UUID 复用和消息一致性规则以 OpenEvent SDK API 为准。
- `parse_message(message)` 只解析 Cmd 业务消息，返回 `ParsedMessage`，包含顶层 seq、channel_id、principal、recipients、
  `object_keys` 元组和已校验的 payload 模型。
  非法 payload 抛出 `CmdSDKError`，可读取其 `code` 和 `message`；OpenEvent RPC 错误保持原异常。
- 读取输出内容时，同时读取 `content_encoding`；请求的字节范围必须能完整返回，见[精确读取规则](CMD_PROTOCOL_cn.md#8-cmdoutputreadrequest)。

## Worker 发布接口

`request_principal` 是原请求的发送方，发布时自动设置 `recipients=[request_principal]`。

```text
client.publish_run_accepted(principal, token, channel_id, request_principal, prev_seq, *, uuid=None)
client.publish_run_result(principal, token, channel_id, request_principal, result_payload, *, uuid=None)
client.publish_kill_result(principal, token, channel_id, request_principal, result_payload, *, uuid=None)
client.publish_output_read_result(principal, token, channel_id, request_principal, result_payload, *, uuid=None)
```

三个 `result_payload` 分别使用 `CmdRunResultInput`、`CmdKillResultInput` 和 `CmdOutputReadResultInput`。
构造时，`CmdRunResultInput.timeout_ms`、`CmdKillResultInput.target_seq`，以及
`CmdOutputReadResultInput` 的 `target_seq`、`stream`、`offset`、`nbytes` 都必须显式提供，不能省略。
字段含义和状态要求见[运行结果](CMD_PROTOCOL_cn.md#5-cmdrunresult)、[kill 结果](CMD_PROTOCOL_cn.md#7-cmdkillresult)
和[输出读取结果](CMD_PROTOCOL_cn.md#9-cmdoutputreadresult)。
