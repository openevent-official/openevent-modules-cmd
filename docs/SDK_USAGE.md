# Cmd 协议 SDK 用法

> 版本：v0.1  
> 范围：发布、解析和处理 `cmd.v1` 消息的 Python 调用方。

协议规格见 [CMD_PROTOCOL.md](CMD_PROTOCOL.md)。本文描述 Python SDK 的公开接口。

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
    req=CmdRunRequestInput(command="echo hello", workdir="/absolute/path/to/workdir", timeout_ms=300000),
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
parsed = client.parse_message(message)
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

- 发布辅助函数统一使用 OpenEvent `PublishAutoSeq`。
- `cmd.run.request`、`cmd.kill.request` 和 `cmd.output.read.request` 发布时 `recipients` 为空。
- cmd worker 使用 OpenEvent `Fetch` 拉取消息时必须使用 `only_my_recipient=false`，否则会漏掉
  `recipients` 为空的请求消息。若当前 `openevent-sdk` 的 `fetch` 支持 `channels` 参数，worker 会把配置的
  `channel_ids` 传给 `channels`；若当前已安装 SDK 尚不支持该参数，worker 会拉取调用方可见消息后在本地按
  `channel_ids` 过滤。
- `CmdRunRequestInput.workdir` 如果传入，必须是绝对路径。
- `cmd.run.accepted` 和 result 发布辅助函数面向 `cmd-worker`，调用方需要显式传入 recipients。
- `cmd.run.result` 的 stdout/stderr 对象在 `bytes <= 1024` 时包含 `content`，在
  `bytes > 1024` 时只包含文件元信息。
- `content` 出现时必须同时检查 `content_encoding`；值为 `utf-8` 时内容是 UTF-8 字符串，
  值为 `base64` 时内容是原始字节的 base64 字符串。
- `cmd.output.read.result.content` 总是写入本次局部读取的内容；目标文件不存在时为空字符串，
  `content_encoding` 为 `utf-8`。
- 同一 channel 内成员都可以发布 `cmd.output.read.request` 读取该 channel 内的命令输出。
- SDK 校验 `cmd.output.read.request.nbytes` 时，只检查它是正整数；实际返回大小由 worker 根据
  OpenEvent payload 上限控制。
- SDK 需要能解析各类 result 的 `error_message`；`REJECTED`、`START_FAILED`、`READ_FAILED` 和
  `RECOVERY_FAILED` 等状态会使用该字段说明原因。
- SDK 或 worker 做重复确认/result 判断时，以同一 channel 内的 `kind + prev_seq` 为准。
- SDK 负责校验 payload 结构；channel 归属、命令安全和权限边界由部署负责。
