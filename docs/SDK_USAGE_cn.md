# Cmd 协议 SDK 用法

[English version](SDK_USAGE.md)

消息格式见[协议](CMD_PROTOCOL_cn.md)。

## 公开接口

请使用部署环境中的 Cmd 通道和凭据。下面的命令向 stdout 写入 6 个字节。

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

从运行请求的下一条序号开始订阅，既能收到已经写回的结果，也能等到后续消息。
收到这条运行请求的 `SUCCESS` 后，再请求读取 6 个字节并打印回复。
解析 payload 前先跳过 [OpenEvent 系统消息](CONFIGURATION_cn.md#channel-要求)。

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

运行结果本身也包含 `stdout.file` 和 `stdout.bytes`。短输出还带有 `stdout.content` 和 `stdout.content_encoding`。

要单独试用取消功能，可以提交一条耗时命令，再用它自己的序号取消任务。

```python
long_run_seq = client.publish_run_request(
    principal, token, channel_id, CmdRunRequestInput(command="sleep 60"),
)
kill_seq = client.publish_kill_request(
    principal, token, channel_id, CmdKillRequestInput(target_seq=long_run_seq),
)
```

## 集成说明

- 发布方法返回消息的整数 seq，使用 OpenEvent `PublishAutoSeq`；请求方法自动使用空 recipients。
- `publish_run_request(..., object_keys=())` 接收可迭代的 OpenEvent SDK `ObjectKey`，写入消息顶层。
  命令需要附件时，用 `write_object` 返回的 `object_id` 和 `object_token` 构造
  `openevent.sdk.proto.openevent_pb2.ObjectKey`，放入 `attachments` 列表。
  子进程取得凭据的方式见[运行请求协议](CMD_PROTOCOL_cn.md#3-cmdrunrequest)。
- 所有发布方法支持可选关键字参数 `uuid`。省略时从已安装的 OpenEvent SDK 分配 UUID；指定时使用传入值。
  对运行请求，像上例那样在发布前保留 UUID、请求和附件，直到拿到 `run_seq`。若超时或响应丢失，
  可用相同 UUID 和原请求重试。若返回 `ALREADY_EXISTS`，调用
  `openevent_client.get_seq_by_uuid(run_uuid)` 取得已提交的任务序号。换用新 UUID 重试可能产生第二条运行请求。
  本辅助层不自动重试。
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
