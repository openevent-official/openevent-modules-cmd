# Cmd 协议 cmd.v1

[English version](CMD_PROTOCOL.md)

## 1. 通道

cmd 通道必须设置 `protocol="cmd.v1"`，不能使用 public visibility。调用方和 worker principal
都必须是通道成员，同一通道同一时刻只能有一个有效 worker 消费。

`description` 是可选的普通说明文字，可以为空；worker 不解析其内容。

## 2. 公共规则

所有 payload 都是 UTF-8 JSON 对象，未知字段视为非法。公共字段：

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

非法 payload 能识别请求类型时，worker 写对应的 `status="REJECTED"` result；无法识别时，记录错误并忽略。
所有 `REJECTED` result 必须用 `error_message` 说明原因。worker 的错误说明最多为 512 个 UTF-8 字节，
超出时在完整字符边界截短。

`max_payload_bytes` 指 OpenEvent 部署的消息 payload 大小上限，参考实现默认为 16 MiB；
本文所有消息大小限制以实际部署值为准。worker 支持的最小值见[配置文档](CONFIGURATION_cn.md#字段)。
所有 accepted/result 发布前检查最终编码大小；局部读取按第 8、9 节缩小返回量，其他无法容纳的结果
导致 worker 报错退出。

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
- 请求非法、获得执行槽位前超时、或在队列中被 kill 时，不写 accepted，分别直接写
  `REJECTED`、`TIMEOUT`、`KILLED` 运行结果。

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
  "finished_at_ms": 1710000001200,
  "stdout": {"file": "2001/12345.stdout", "bytes": 6, "content_encoding": "utf-8", "content": "hello\n"},
  "stderr": {"file": "2001/12345.stderr", "bytes": 0, "content_encoding": "utf-8", "content": ""}
}
```

- `timeout_ms` 是按第 3 节确定的有效超时；除 `REJECTED` 且无法确定有效值外必须出现。
  `RECOVERY_FAILED` 使用请求显式值或恢复时的当前默认值。
- `finished_at_ms` 必填，是 worker 写出最终运行结果的时间；`started_at_ms` 只在进程实际启动后出现。
- `exit_code` 是 shell 退出码，只在 `SUCCESS` 或 `FAILED` 时出现。
- `START_FAILED`、`RECOVERY_FAILED` 必须写 `error_message`。
- 启动前超时、队列中被 kill、请求非法或启动失败时，stdout/stderr 写空输出对象。
  `RECOVERY_FAILED` 按已有输出文件生成输出对象，文件不存在时写空输出对象。

`status` 取值：

| 状态 | 含义 |
| --- | --- |
| `SUCCESS` | 未因 worker 超时或 kill 请求终止，shell 退出码为 0 |
| `FAILED` | 未因 worker 超时或 kill 请求终止，shell 退出码非 0 |
| `TIMEOUT` | 因超时杀掉进程，或启动前请求已经超时 |
| `KILLED` | 因 kill 请求杀掉进程，或从队列删除未启动任务 |
| `START_FAILED` | 启动准备或进程启动失败，包括输出子目录或文件无法创建、文件无法打开、工作目录无法进入、shell 无法启动 |
| `RECOVERY_FAILED` | 启动扫描发现历史运行请求已有 accepted 但没有最终 result，补写失败 result |
| `REJECTED` | 请求 payload 非法或超过协议/配置限制，未执行命令 |

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
  `target_seq` 指向其他通道时返回 `NOT_FOUND`，不跨通道操作。

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

`target_seq` 必须回显原请求中的合法值；原值非法或缺失时，`REJECTED` 可以省略该字段。

`status` 取值：

| 状态 | 含义 |
| --- | --- |
| `KILL_SENT` | 目标 shell 仍在运行，已向任务进程组发送 kill 信号 |
| `REMOVED_PENDING` | 目标尚未启动，已从队列删除 |
| `ALREADY_FINISHED` | 目标已有最终运行结果，或 shell 已退出并完成组清理与回收；后一种情形先写运行结果 |
| `NOT_FOUND` | 没有找到目标任务 |
| `REJECTED` | 请求 payload 非法或超过协议/配置限制，未发送 kill |

kill 先触发终止时，运行结果为 `KILLED`；超时已先触发时保留 `TIMEOUT`。
删除待执行任务时，先确认写入运行结果 `KILLED`，再写 `REMOVED_PENDING`。
两条结果之间崩溃时，重启后运行请求保持已闭合，未完成的 kill 请求可以返回 `ALREADY_FINISHED`。

shell 已退出但尚未收尾时，先按[任务进程生命周期](CONFIGURATION_cn.md#任务进程生命周期)完成清理和回收，
写运行结果，再写 `ALREADY_FINISHED`。运行结果保留已确定的 `TIMEOUT`/`KILLED`；否则按 shell
退出码写 `SUCCESS`/`FAILED`，清理组内残留进程不改为 `KILLED`。

## 8. `cmd.output.read.request`

读取 worker 本地 `output_dir/<channel_id>/<target_seq>.stdout` 或 `.stderr` 的一段内容，不改变任务状态。

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

- `target_seq` 必填，是目标 `cmd.run.request` 的 OpenEvent `seq`。
- `stream` 必填，只能是 `stdout` 或 `stderr`。
- `offset` 必填，是非负整数，表示读取起始字节位置。
- `nbytes` 必填，是正整数，表示本次最多读取的字节数。
- JSON 和必要的 base64 编码后若会超过部署的 payload 上限，worker 必须减少实际读取量。
  返回的 `bytes` 可以小于 `nbytes`；调用方用 `offset + bytes` 继续读取。
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
  "nbytes": 1024,
  "bytes": 6,
  "eof": true,
  "content_encoding": "utf-8",
  "content": "hello\n"
}
```

- `target_seq`、`stream`、`offset`、`nbytes` 必须回显原请求中的合法值；原值非法或缺失时，
  `REJECTED` 可以省略相应字段。
- `status`、`content`、`content_encoding` 必填；`bytes` 是返回内容的原始字节数，未读取时为 0。
- worker 打开文件后先取得当时的大小；`eof` 为 `offset + bytes >= 初始文件大小`。
  缩小返回量也按此规则判断。`eof=true` 不保证运行中的任务以后不会追加输出。
- `offset` 已到文件末尾时，返回空内容、`bytes=0`、`eof=true`。

`status` 取值及失败字段：

| 状态 | 含义 | `eof` | `error_message` |
| --- | --- | --- | --- |
| `SUCCESS` | 读取成功 | 按上述规则 | — |
| `NOT_FOUND` | 目标输出文件不存在 | `true` | — |
| `READ_FAILED` | 文件存在但无法 stat、打开或读取 | `false` | 必填 |
| `REJECTED` | 请求 payload 非法或超过协议/配置限制，未读取文件 | `true` | 必填 |

后三种状态均写 `content=""`、`bytes=0`、`content_encoding="utf-8"`。

## 10. 版本策略

破坏性变更必须使用新协议，例如 `cmd.v2`。
