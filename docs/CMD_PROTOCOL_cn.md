# Cmd 协议 cmd.v3

[English version](CMD_PROTOCOL.md)

## 1. 通道

cmd 通道必须设置 `protocol="cmd.v3"`，不能使用 public visibility。调用方和 worker principal
都必须是通道成员，同一通道同一时刻只能有一个有效 worker 消费。

`description` 是可选的普通说明文字，可以为空；worker 不解析其内容。

## 2. 公共规则

以下规则只适用于 Cmd 业务消息；OpenEvent 系统消息的处理见[运行说明](CONFIGURATION_cn.md#channel-要求)。
所有 Cmd payload 都是 UTF-8 JSON 对象，未知字段视为非法。公共字段：

- `kind`：必填，取值为本文各消息章节标题中的类型。
- `ts_ms`：必填，payload 生成方写入的 Unix 毫秒时间戳。

身份与路由信息使用 OpenEvent 顶层字段。顶层 `EventMessage.ts_ms` 是服务端收到消息写入请求时
生成的 Unix 毫秒时间戳，与 payload 的生成时间不同。

所有请求消息的 OpenEvent `principal` 是调用方 principal，`recipients` 必须为空。

所有 accepted/result 消息的 OpenEvent `principal` 是 worker principal，`recipients` 是对应请求的
发送方 principal；payload 中的 `prev_seq` 必填，等于对应请求的 OpenEvent `seq`。

输出的 `content_encoding` 为 `utf-8` 或 `base64`：原始字节能按 UTF-8 解码时，`content` 写解码后的
字符串；否则写原始字节的 base64 字符串。两字段必须同时出现。空输出使用 `bytes=0`、
`content_encoding="utf-8"`、`content=""`。

非系统消息的 payload 无法解码为 UTF-8 JSON 对象，或消息类型、字段校验失败时，worker 报错退出，
不跳过消息，也不为它写回 Cmd 结果。此规则同时适用于正常消费和启动恢复中的请求、accepted 和 result。
退出与清理行为见[运行说明](CONFIGURATION_cn.md#恢复与退出)。

`START_FAILED`、`RECOVERY_FAILED` 和 `READ_FAILED` 的 `error_message` 最多为 512 个 UTF-8 字节，
超出时在完整字符边界截短。

`max_payload_bytes` 指 OpenEvent 部署的消息 payload 大小上限，参考实现默认为 16 MiB；
本文所有消息大小限制以实际部署值为准。worker 支持的最小值见[配置文档](CONFIGURATION_cn.md#字段)。
所有 accepted/result 发布前检查最终编码大小；局部读取的成功结果无法容纳时，按第 8、9 节返回
`READ_FAILED`，不缩小返回量。其他无法容纳的结果导致 worker 报错退出。

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

- 消息的 OpenEvent `seq` 是命令任务 ID。
- `command` 必填，是包含完整 Linux 命令行的非空字符串。worker 默认通过 `/bin/sh -c <command>` 执行；
  进程管理范围见[任务进程生命周期](CONFIGURATION_cn.md#任务进程生命周期)。不支持独立的 `env` 或 `stdin` 字段。
- `workdir` 可选，必须是非空的绝对路径字符串；缺失时使用 worker 当前工作目录。
  路径不存在、不是目录、无权进入或导致 shell 无法启动时，写 `START_FAILED` 和 `error_message`，不视为非法 payload。
- `timeout_ms` 可选，必须是 `1..18446744073709551615`（`2**64-1`）范围内的整数。
  缺失时使用处理请求时的当前默认值；[启动恢复](CONFIGURATION_cn.md#恢复与退出)也使用当前配置，
  包括补写 `RECOVERY_FAILED`。
- 超时截止时间为本条消息的顶层 `EventMessage.ts_ms` 加有效 `timeout_ms`，不能用 payload 的 `ts_ms`。

调用方可以在本条消息的 OpenEvent 顶层 `object_keys` 中附带命令需要的对象读取凭据；它不是 payload 字段。
worker 把这些凭据交给本次命令的运行环境，命令不需要向调用方回查凭据。具体规则：

- worker 继承自己的进程环境，并为本次 shell 设置 `OPENEVENT_OBJECT_KEYS`。
- 值为 JSON 数组，顺序与请求一致，每项含整数 `object_id` 和字符串 `object_token`，保留完整 ObjectKey。
  没有附件时固定设置为 `[]`，覆盖 worker 环境中可能已有的值；不同任务不共享或累计凭据。
- 该变量随进程环境传给命令及其子进程，可以重复读取；worker 不把凭据拼进 `command`，也不主动输出凭据。
- 对象服务地址由部署环境提供，Cmd 不负责下载对象或提供附件读取程序。
- 凭据与其他进程环境共同受操作系统的进程参数、环境大小限制；超过限制而无法启动时返回 `START_FAILED`。
  其他请求及 accepted/result 不传递任务附件凭据。

例如，两个附件对应的环境变量值为：

```json
[{"object_id":123,"object_token":"object-token-123"},{"object_id":456,"object_token":"object-token-456"}]
```

## 4. `cmd.run.accepted`

worker 接手合法且未超时的运行请求时，必须先确认写入 accepted，再启动命令。此后写最终
`cmd.run.result`；未完成的请求在启动恢复时补失败 result。

```json
{
  "kind": "cmd.run.accepted",
  "prev_seq": 12345,
  "ts_ms": 1710000000050
}
```

- accepted 确认后再次检查超时；若发布期间截止时间已到，不启动进程，写 `TIMEOUT`。
  因此 accepted 不保证命令已经启动。
- 获得执行槽位前超时或在队列中被 kill 时，不写 accepted，分别直接写 `TIMEOUT`、`KILLED` 运行结果。

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

- `timeout_ms` 必填，是按第 3 节确定的有效超时。
  `RECOVERY_FAILED` 使用请求显式值或恢复时的当前默认值。
- 公共字段 `ts_ms` 表示本条结果的生成时间，不表示命令实际结束时间。
  `started_at_ms` 表示进程实际启动时间，只在进程实际启动后出现。
- `exit_code` 是 shell 退出码，只在 `SUCCESS` 或 `FAILED` 时出现。
- `START_FAILED`、`RECOVERY_FAILED` 必须写 `error_message`。
- 启动前超时、队列中被 kill 或启动失败时，stdout/stderr 写空输出对象。
  `RECOVERY_FAILED` 按已有输出文件生成输出对象，文件不存在时写空输出对象。

`status` 取值：

| 状态 | 含义 |
| --- | --- |
| `SUCCESS` | 未因 worker 超时或 kill 请求终止，shell 退出码为 0 |
| `FAILED` | 未因 worker 超时或 kill 请求终止，shell 退出码非 0 |
| `TIMEOUT` | 因超时杀掉进程，或启动前请求已经超时 |
| `KILLED` | 因 kill 请求杀掉进程，或从队列删除未启动任务 |
| `START_FAILED` | 启动流程失败，包括输出子目录或文件无法创建、文件无法打开、worker 侧输出文件句柄关闭失败、工作目录无法进入、shell 无法启动 |
| `RECOVERY_FAILED` | 启动扫描发现历史运行请求已有 accepted 但没有最终 result，补写失败 result |

### 5.1 stdout/stderr 输出对象

stdout 和 stderr 独立使用相同规则：

- `file` 必填，是相对 worker 配置 `output_dir` 的路径，固定为 `<channel_id>/<prev_seq>.stdout`
  或 `<channel_id>/<prev_seq>.stderr`；`channel_id` 来自 OpenEvent 顶层字段。
- `bytes` 必填。输出不超过 1024 字节时，必须写 `content` 和 `content_encoding`，包含本次读取范围的完整内容；
  超过时只写文件元信息，不得写 `content`。
- worker 打开文件后通过同一文件句柄取得初始大小。短输出最多读取该范围，忽略后来追加的字节，
  文件缩短时只包含实际读到的内容；`bytes` 是实际读取量。长输出的 `bytes` 是初始文件大小。
- 空输出对象不保证 `file` 对应文件存在，后续局部读取按实际文件是否存在返回结果。

长输出示例：

```json
{
  "file": "2001/12345.stdout",
  "bytes": 2048
}
```

生成运行结果时，文件不存在则写空输出对象；其他打开、取大小或读取错误导致 worker 报错退出，
不发布本次运行结果。下次启动按[恢复规则](CONFIGURATION_cn.md#恢复与退出)处理未闭合请求；
恢复期间读取失败仍退出。局部读取请求的错误行为见第 9 节。

## 6. `cmd.kill.request`

```json
{
  "kind": "cmd.kill.request",
  "ts_ms": 1710000000500,
  "target_seq": 12345
}
```

- `target_seq` 必填，是正整数，预期指向 `cmd.run.request` 的 OpenEvent `seq`。
- worker 只终止当前进程内运行中或待执行的任务；待执行任务直接从队列删除。
- 同一通道的任何成员都可以 kill 该通道内的任务，无须是原提交者。
  `target_seq` 指向其他通道时返回 `NOT_RUNNING`，不跨通道操作。

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

`target_seq` 必填，必须回显原请求中的值。本消息不包含 `error_message` 字段。

`status` 取值：

| 状态 | 含义 |
| --- | --- |
| `KILL_SENT` | 目标 shell 仍在运行，已向任务进程组发送 kill 信号 |
| `REMOVED_PENDING` | 目标尚未启动，已从队列删除 |
| `NOT_RUNNING` | 当前通道没有可终止的运行中或待执行目标，不区分已完成和从未存在 |

kill 先触发终止时，运行结果为 `KILLED`；超时已先触发时保留 `TIMEOUT`。
删除待执行任务时，先确认写入运行结果 `KILLED`，再写 `REMOVED_PENDING`。
两条结果之间崩溃时，重启后运行请求保持已闭合，未完成的 kill 请求返回 `NOT_RUNNING`。

shell 已退出但尚未收尾时，先按[任务进程生命周期](CONFIGURATION_cn.md#任务进程生命周期)完成清理和回收，
写运行结果，再写 `NOT_RUNNING`。运行结果保留已确定的 `TIMEOUT`/`KILLED`；否则按 shell
退出码写 `SUCCESS`/`FAILED`，清理组内残留进程不改为 `KILLED`。

## 8. `cmd.output.read.request`

精确读取 worker 本地 `output_dir/<channel_id>/<target_seq>.stdout` 或 `.stderr` 的指定字节范围，不改变任务状态。

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

- `target_seq` 必填，是目标 `cmd.run.request` 的 OpenEvent `seq`。
- `stream` 必填，只能是 `stdout` 或 `stderr`。
- `offset` 必填，是非负整数，表示读取起始字节位置。
- `nbytes` 必填，是正整数，表示本次必须读取的精确字节数；请求范围为 `[offset, offset + nbytes)`。
- worker 必须完整返回该范围，不得缩小读取量或返回部分内容。请求范围超过打开后取得的初始文件大小、
  实际读取不足 `nbytes`，或完整结果经 JSON 和必要的 base64 编码后超过部署的 payload 上限时，
  返回 `READ_FAILED` 并说明原因。文件为空，或 `offset` 已到达或超过文件末尾时，同样返回 `READ_FAILED`。
- 同一通道的任何成员都可以读取该通道的输出。`target_seq` 指向其他通道时返回 `NOT_FOUND`，不跨通道查找。

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

- `target_seq`、`stream`、`offset`、`nbytes` 必填，必须回显原请求中的值。
- `status`、`content`、`content_encoding` 必填；成功时 `bytes` 必须等于请求的 `nbytes`，
  `content` 解码后必须是请求范围内的完整原始字节。
- worker 打开文件后先取得当时的大小；成功时 `eof` 为 `offset + nbytes == 初始文件大小`。
  `eof=true` 不保证运行中的任务以后不会追加输出。

`status` 取值及失败字段：

| 状态 | 含义 | `eof` | `error_message` |
| --- | --- | --- | --- |
| `SUCCESS` | 读取成功 | 按上述规则 | — |
| `NOT_FOUND` | 目标输出文件不存在 | `true` | — |
| `READ_FAILED` | 文件存在但无法 stat、打开或读取，或无法满足第 8 节的精确范围及消息大小要求 | `false` | 必填 |

后两种状态均写 `content=""`、`bytes=0`、`content_encoding="utf-8"`。

## 10. 版本策略

本包实现 `cmd.v3`，通道和调用方须使用该协议。
破坏性变更必须使用新协议，例如 `cmd.v4`。
