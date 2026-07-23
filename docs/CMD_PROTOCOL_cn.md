# Cmd 协议 cmd.v1

[English version](CMD_PROTOCOL.md)

> 状态：v0.1 初始发布
> 范围：OpenEvent 通道 `protocol="cmd.v1"` 的 Linux 命令任务 payload 与通道 description。

## 1. 通道

所有 cmd channel 必须设置：

```text
protocol = "cmd.v1"
```

`description` 必须是 JSON 字符串：

```json
{
  "version": "v1",
  "updated_at_ms": 1710000000000,
  "metadata": {}
}
```

字段规则：

- `version`：字符串，当前固定为 `v1`。
- `updated_at_ms`：整数，Unix 毫秒时间戳。
- `metadata`：可选对象，用于部署或业务域静态元数据。

cmd 通道不能使用 public visibility。业务调用方和 cmd worker principal 必须都是
通道成员。多 worker 冲突不由 `cmd.v1` 协议处理；部署方必须保证同一个通道同一时刻只有一个
有效 cmd worker 进程消费。

## 2. 公共规则

所有 `cmd.v1` payload 都是 UTF-8 JSON 对象。未知字段视为非法。

本文把 OpenEvent 部署的消息 payload 大小上限记为 `max_payload_bytes`。该上限由 OpenEvent
部署决定；参考实现默认值为 16 MiB。cmd 协议中和消息大小相关的限制都按实际部署值计算。

公共字段：

- `kind`：必填，取值为 `cmd.run.request`、`cmd.run.result`、
  `cmd.run.accepted`、`cmd.kill.request`、`cmd.kill.result`、
  `cmd.output.read.request` 或 `cmd.output.read.result`。
- `ts_ms`：必填，payload 生成方写入的 Unix 毫秒时间戳。

OpenEvent 顶层 `principal`、`channel_id`、`seq`、`ts_ms` 和 `recipients` 承载身份、服务端写入时间
与路由信息，不在 payload 内重复存储。
OpenEvent 顶层 `EventMessage.ts_ms` 是服务端收到该消息写入请求时生成的 Unix 毫秒时间戳。
cmd 协议涉及命令超时计算时，必须使用对应 `cmd.run.request` 的 OpenEvent 顶层
`EventMessage.ts_ms`，不能使用 payload 内的 `ts_ms`。

非法 payload 但能识别出对应请求类型时，worker 必须写对应 result，并通过
`status="REJECTED"` 表示拒绝；如果 payload 损坏到无法识别请求类型，worker 只能记录错误并忽略。
所有 `status="REJECTED"` 的 result 都必须写 `error_message`，用于说明拒绝原因。

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

规则：

- OpenEvent `principal` 是业务调用方 principal。
- OpenEvent `recipients` 必须为空。
- `command` 必填，是非空字符串，表示要执行的完整 Linux 命令行。
- worker 使用 Linux shell 执行 `command`；默认等价于 `/bin/sh -c <command>`。
- `workdir` 可选，是非空字符串，表示命令执行工作目录；缺失时使用 worker 当前工作目录。
- `workdir` 如果出现，必须是绝对路径。
- `workdir` 是绝对路径但不存在、不是目录、无权限进入，或导致 shell 进程无法启动时，payload 本身不视为非法；
  worker 写 `cmd.run.result(status=START_FAILED)` 和 `error_message`。
- `timeout_ms` 可选，必须是正整数；缺失时 worker 使用配置里的默认超时。
- `timeout_ms` 缺失时始终使用 worker 处理该请求时的当前默认超时配置；启动恢复处理历史未 accepted
  run request 时也是如此。
- 超时截止时间按该 `cmd.run.request` 消息的 OpenEvent 顶层 `EventMessage.ts_ms` 加 `timeout_ms` 计算；
  缺失 `timeout_ms` 时使用配置里的默认超时。payload 内的 `ts_ms` 不作为超时起点。
- `cmd.run.request` 不支持独立的 `env` 或 `stdin` 字段。
- 该 OpenEvent 消息的 `seq` 是命令任务 ID。

## 4. `cmd.run.accepted`

worker 准备启动合法且未超时的 `cmd.run.request` 时，必须在启动命令进程前写入确认消息。
该消息表示 worker 已经接手并即将启动该 run request，后续要么写最终 `cmd.run.result`，
要么在启动恢复时被补失败 result。

```json
{
  "kind": "cmd.run.accepted",
  "prev_seq": 12345,
  "ts_ms": 1710000000050
}
```

规则：

- OpenEvent `principal` 是 cmd worker principal。
- OpenEvent `recipients` 是原始 `cmd.run.request` 的发送方 principal。
- `prev_seq` 必填，必须等于对应 run request 的 OpenEvent seq。
- `cmd.run.accepted` 必须在启动命令进程前写入。
- 如果 run request 非法，worker 不写 `cmd.run.accepted`，直接写
  `cmd.run.result(status=REJECTED)`。
- 如果 run request 在获得执行槽位前已经超时，worker 不写 `cmd.run.accepted`，直接写
  `cmd.run.result(status=TIMEOUT)`。
- 如果 run request 在待执行队列中被 kill 删除，worker 不写 `cmd.run.accepted`，直接写
  `cmd.run.result(status=KILLED)`。

## 5. `cmd.run.result`

stdout/stderr 小于等于 1k 时直接写内容，大于 1k 时只写文件元信息。本文中的 1k 固定按
1024 字节计算，stdout 和 stderr 分别独立判断。

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

规则：

- OpenEvent `principal` 是 cmd worker principal。
- OpenEvent `recipients` 是原始 `cmd.run.request` 的发送方 principal。
- `prev_seq` 必填，必须等于对应 run request 的 OpenEvent seq。
- worker 按 OpenEvent 顶层 `channel_id` 建立输出子目录，不同 channel 的输出文件必须分开。
- `stdout.file` 固定为 `<channel_id>/<prev_seq>.stdout`；`stderr.file` 固定为
  `<channel_id>/<prev_seq>.stderr`。这里的 `file` 是相对 `output_dir` 的路径。
- `stdout` 和 `stderr` 分别独立判断输出大小，阈值为 1024 字节：
  - `bytes <= 1024` 时必须写 `content`，内容为对应输出文件的完整内容。
  - `bytes > 1024` 时不得写 `content`，只写文件元信息。
- 实际输出目录由 worker 配置 `output_dir` 决定。
- `timeout_ms` 是本次运行请求使用的有效超时时间；除 `status="REJECTED"` 且无法确定有效超时外必须出现。
- `finished_at_ms` 是 worker 写出最终运行结果的时间，所有 `cmd.run.result` 都必须出现。
- `started_at_ms` 只在命令进程实际启动后出现。
- `exit_code` 只在 `status="SUCCESS"` 或 `status="FAILED"` 时出现。
- `error_message` 在 `status="REJECTED"`、`status="START_FAILED"` 或 `status="RECOVERY_FAILED"` 时必须出现。
- `status="REJECTED"` 时 worker 不启动命令，stdout/stderr 写空输出对象。
- 启动前已经超时、待执行队列中被 kill、请求非法或启动失败时，stdout/stderr 都写空输出对象。
- 启动失败包括：channel 输出子目录无法创建、stdout/stderr 文件无法创建或打开、`workdir` 无法进入、
  shell 进程无法启动。
- `status="RECOVERY_FAILED"` 时，如果原 stdout/stderr 文件存在，按已有文件写输出对象；如果不存在，写空输出对象。
- 空输出对象只表示本条 result 中输出为空，不要求对应 stdout/stderr 文件一定存在。

### 5.1 stdout/stderr 输出对象

stdout 和 stderr 使用相同结构。`file` 和 `bytes` 必填；`content` 是否出现由该输出流自己的
`bytes` 决定。出现 `content` 时，必须同时写 `content_encoding`。

短输出示例，`bytes <= 1024`：

```json
{
  "file": "2001/12345.stdout",
  "bytes": 6,
  "content_encoding": "utf-8",
  "content": "hello\n"
}
```

长输出示例，`bytes > 1024`：

```json
{
  "file": "2001/12345.stdout",
  "bytes": 2048
}
```

空输出示例：

```json
{
  "file": "2001/12345.stdout",
  "bytes": 0,
  "content_encoding": "utf-8",
  "content": ""
}
```

规则：

- `file` 是相对 `output_dir` 的路径，不包含 `output_dir` 本身。
- `bytes` 是该输出文件的字节数。
- `content` 是短输出的完整内容；长输出不得写 `content`。
- `content_encoding` 只在 `content` 出现时出现，取值为 `utf-8` 或 `base64`。
- 如果输出字节能按 UTF-8 解码，`content_encoding="utf-8"`，`content` 写解码后的字符串。
- 如果输出字节不能按 UTF-8 解码，`content_encoding="base64"`，`content` 写原始字节的 base64 字符串。
- 空输出对象必须写 `bytes=0`、`content_encoding="utf-8"` 和 `content=""`。
- 空输出对象不表示 `file` 指向的文件一定存在；后续 `cmd.output.read.request` 仍按实际文件是否存在返回结果。
- stdout 和 stderr 分别独立判断。例如 stdout 为 100 字节、stderr 为 4096 字节时，
  stdout 必须写 `content`，stderr 只写文件元信息。

`status` 取值：

| 状态 | 含义 |
| --- | --- |
| `SUCCESS` | 进程退出码为 0 |
| `FAILED` | 进程退出码非 0 |
| `TIMEOUT` | worker 因超时杀掉进程，或在启动前发现请求已经超时 |
| `KILLED` | worker 因 kill 请求杀掉进程，或从待执行队列中删除未启动任务 |
| `START_FAILED` | 进程启动前准备失败或进程无法启动，包括输出文件无法创建或打开、工作目录无法进入、shell 无法启动 |
| `RECOVERY_FAILED` | worker 启动从头消费时发现历史运行请求已有 `cmd.run.accepted` 但没有最终 result，补写失败 result |
| `REJECTED` | 运行请求 payload 非法或超过协议/配置限制，worker 未执行命令 |

## 6. `cmd.kill.request`

```json
{
  "kind": "cmd.kill.request",
  "ts_ms": 1710000000500,
  "target_seq": 12345
}
```

规则：

- `target_seq` 必填，必须是正整数，预期指向某条 `cmd.run.request` 的 OpenEvent seq。
- worker 首版只处理当前 worker 进程内运行中或待执行的任务。
- 如果目标任务在待执行队列中但尚未启动，worker 直接从待执行队列中删除该任务。
- 同一 `cmd.v1` channel 内的成员都可以 kill 该 channel 内运行中或待执行的任务，不限制必须由原提交者 kill。
- kill 请求只作用于请求所在 channel 内的目标任务；`target_seq` 指向其他 channel 的 run request 时，
  worker 不跨 channel 操作，按 `NOT_FOUND` 返回。

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

规则：

- OpenEvent `principal` 是 cmd worker principal。
- OpenEvent `recipients` 是原始 `cmd.kill.request` 的发送方 principal。
- `prev_seq` 必填，必须等于对应 kill request 的 OpenEvent seq。
- `target_seq` 在能够从原始请求解析出合法值时必须回显；如果原始请求的 `target_seq` 非法或缺失，
  `status="REJECTED"` 时可以省略。
- `status="REJECTED"` 时必须写 `error_message`。

`status` 取值：

| 状态 | 含义 |
| --- | --- |
| `KILL_SENT` | 目标任务仍在运行，worker 已发送 kill 信号 |
| `REMOVED_PENDING` | 目标任务尚未启动，worker 已从待执行队列中删除 |
| `ALREADY_FINISHED` | 目标任务已经结束，worker 未发送 kill；如果尚未写运行结果，worker 先写最终 run result |
| `NOT_FOUND` | worker 没有找到目标任务 |
| `REJECTED` | kill 请求 payload 非法或超过协议/配置限制，worker 未发送 kill |

目标任务仍在运行时，worker 写 `cmd.kill.result(status=KILL_SENT)`，随后目标任务写
`cmd.run.result(status=KILLED)`。目标任务尚未启动且仍在待执行队列中时，worker 写
`cmd.kill.result(status=REMOVED_PENDING)`，并为目标运行请求写 `cmd.run.result(status=KILLED)`。
如果 worker 处理 kill 请求时发现目标进程已经退出但完成事件尚未处理，必须先根据目标进程退出码
为目标运行请求写 `cmd.run.result(status=SUCCESS)` 或 `cmd.run.result(status=FAILED)`，再写
`cmd.kill.result(status=ALREADY_FINISHED)`。

## 8. `cmd.output.read.request`

调用方可以通过消息读取某个命令输出文件的一段内容。该请求只读取 worker 本地
`output_dir/<channel_id>/` 下的 `<target_seq>.stdout` 或 `<target_seq>.stderr`。

```json
{
  "kind": "cmd.output.read.request",
  "ts_ms": 1710000002000,
  "target_seq": 12345,
  "stream": "stdout",
  "offset": 0,
  "nbytes": 1024
}
```

规则：

- OpenEvent `principal` 是查询方 principal。
- OpenEvent `recipients` 必须为空。
- `target_seq` 必填，必须是目标 `cmd.run.request` 的 OpenEvent seq。
- `stream` 必填，只能是 `stdout` 或 `stderr`。
- `offset` 必填，必须是非负整数，表示从输出文件第几个字节开始读。
- `nbytes` 必填，必须是正整数，表示调用方希望本次最多读取多少字节。
- worker 返回的 `bytes` 可以小于 `nbytes`。当读取内容按 JSON 和必要的 base64 编码后会导致
  `cmd.output.read.result` 超过 OpenEvent 部署的 payload 大小上限时，worker 必须减少本次实际读取字节数，
  确保 result payload 可以写入 OpenEvent。调用方用 `offset + bytes` 继续读取后续内容。
- `cmd.output.read.request` 不改变目标任务状态，只读取已有输出文件内容。
- 同一 `cmd.v1` channel 内的成员都可以读取该 channel 内的命令输出。
- 输出局部读取只作用于请求所在 channel 的输出目录；`target_seq` 指向其他 channel 的 run request 时，
  worker 不跨 channel 查找，按 `NOT_FOUND` 返回。
- 字段名使用 `target_seq` 而不是 `seq`，是为了避免和 OpenEvent 顶层消息 `seq` 混淆。

## 9. `cmd.output.read.result`

worker 对局部读取请求写回内容：

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

规则：

- OpenEvent `principal` 是 cmd worker principal。
- OpenEvent `recipients` 是原始 `cmd.output.read.request` 的发送方 principal。
- `prev_seq` 必填，必须等于对应 output read request 的 OpenEvent seq。
- `target_seq`、`stream`、`offset`、`nbytes` 在能够从原始请求解析出合法值时必须回显；如果原始请求中
  某个字段非法或缺失，`status="REJECTED"` 时可以省略无法回显的字段。
- `status` 必填，表示本次局部读取请求的处理结果。
- `bytes` 是实际写入 `content` 的字节数；未读取文件时为 `0`。
- `content` 必填，写入本次读取到的内容；如果 `offset` 已到达读取开始时的当前文件末尾，则
  `content` 为空字符串，`bytes=0`，`eof=true`。
- `content_encoding` 必填，取值为 `utf-8` 或 `base64`。
- 如果读取到的字节能按 UTF-8 解码，`content_encoding="utf-8"`，`content` 写解码后的字符串。
- 如果读取到的字节不能按 UTF-8 解码，`content_encoding="base64"`，`content` 写原始字节的 base64 字符串。
- worker 为保证 result payload 不超过 OpenEvent 部署上限而减少本次读取量时，`bytes` 写实际返回的字节数；
  只要 `offset + bytes` 尚未到达读取开始时的当前文件大小，`eof` 就必须为 `false`。
- `eof` 表示本次读取是否已经到达读取开始时的当前文件末尾。worker 打开文件后先取得当时的文件大小；
  如果 `offset + bytes` 大于或等于该大小，则 `eof=true`。目标任务仍在运行时，`eof=true`
  不表示该任务之后不会继续写入该输出文件。
- 如果目标输出文件不存在，worker 写 `status="NOT_FOUND"`、`content=""`、`bytes=0`、
  `content_encoding="utf-8"`、`eof=true`。
- 如果目标输出文件存在但 worker 无法 stat、打开或读取，worker 写 `status="READ_FAILED"`、
  `content=""`、`bytes=0`、`content_encoding="utf-8"`、`eof=false` 和 `error_message`。
- 如果输出局部读取请求 payload 非法或超过协议/配置限制，worker 写 `status="REJECTED"`、
  `content=""`、`bytes=0`、`content_encoding="utf-8"`、`eof=true` 和 `error_message`，
  且不读取输出文件。

目标文件存在时的成功示例：

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

目标文件不存在时的结果示例：

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

`status` 取值：

| 状态 | 含义 |
| --- | --- |
| `SUCCESS` | 读取成功，`content` 是读取到的内容 |
| `NOT_FOUND` | 目标输出文件不存在 |
| `READ_FAILED` | 目标输出文件存在但 worker 无法 stat、打开或读取 |
| `REJECTED` | 输出局部读取请求 payload 非法或超过协议/配置限制，worker 未读取输出文件 |

## 10. 版本策略

- `cmd.v1` 不考虑旧 worker 对新字段的兼容；协议字段变化时，文档、SDK 和 worker 同步更新。
- 破坏性变更必须使用新协议，例如 `cmd.v2`。

## 11. 已确认协议决策

- kill 请求和输出局部读取请求的目标任务字段固定使用 `target_seq`。
- `target_seq` 表示目标 `cmd.run.request` 的 OpenEvent seq，用于避免和 OpenEvent 顶层消息
  `seq` 混淆。
