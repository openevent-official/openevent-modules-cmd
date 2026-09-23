# Cmd 协议 SDK 用法

[English version](SDK_USAGE.md)

> 版本：v0.1
> 范围：发布、解析和处理 `cmd.v1` 消息的 Python 调用方。

协议规格见 [CMD_PROTOCOL_cn.md](CMD_PROTOCOL_cn.md)。本文描述 Python SDK 的公开接口。

## 公开接口

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

解析消息：

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

## 集成说明

- 发布方法返回消息的整数 seq，使用 OpenEvent `PublishAutoSeq`；请求方法自动使用空 recipients。
- 所有发布方法支持可选关键字参数 `uuid`。省略时从已安装的 OpenEvent SDK 分配 UUID；指定时使用传入值。
  本辅助层不自动重试。UUID 复用和消息一致性规则以 OpenEvent SDK API 为准。
- `parse_message(message)` 返回 `ParsedMessage`，包含顶层 seq、channel_id、principal、recipients 和已校验的 payload 模型。
  非法 payload 抛出 `CmdSDKError`，可读取其 `code` 和 `message`；OpenEvent RPC 错误保持原异常。
- 输出内容、编码、状态和字段规则统一见[协议](CMD_PROTOCOL_cn.md)，读取内容时应同时读取 `content_encoding`。
- worker 使用 `Fetch(only_my_recipient=false, channels=channel_ids)` 获取配置通道的请求和结果。

## Worker 发布接口

以下方法面向 worker。`request_principal` 是原请求的发送方，辅助函数自动写成
`recipients=[request_principal]`，没有名为 `recipients` 的参数。

```text
client.publish_run_accepted(principal, token, channel_id, request_principal, prev_seq, *, uuid=None)
client.publish_run_result(principal, token, channel_id, request_principal, result_payload, *, uuid=None)
client.publish_kill_result(principal, token, channel_id, request_principal, result_payload, *, uuid=None)
client.publish_output_read_result(principal, token, channel_id, request_principal, result_payload, *, uuid=None)
```

上面列出的是方法签名。三个 `result_payload` 分别使用 `CmdRunResultInput`、`CmdKillResultInput`、
`CmdOutputReadResultInput`；发布函数与请求函数一样返回整数 seq。
