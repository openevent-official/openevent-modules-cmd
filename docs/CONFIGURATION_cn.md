# 配置

[English version](CONFIGURATION.md)

通过 `--config` 指定 YAML 配置文件：

```bash
cmd-worker --config cmd-worker.yaml
```

示例文件：[cmd-worker.yaml](../examples/cmd-worker.yaml)。

## 示例

```yaml
open_event:
  addr: 127.0.0.1:9527
  max_payload_bytes: 16777216

principal: 30001
token: token-xxx

channel_ids:
  - 2001
  - 2002

output_dir: cmd-worker-output
max_concurrent_tasks: 8
default_timeout_ms: 300000
fetch_idle_sleep_ms: 100
fetch_error_max_retries: 3
fetch_error_retry_sleep_ms: 1000
```

## 字段

| 字段 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `open_event.addr` | 是 | 无 | OpenEvent 服务地址 |
| `open_event.max_payload_bytes` | 否 | `16777216` | OpenEvent 部署的消息 payload 大小上限，必须是至少 `16384`（16 KiB）的整数；所有 accepted/result 发布前检查最终大小，局部读取据此缩小返回量 |
| `principal` | 是 | 无 | cmd-worker 使用的 OpenEvent principal，必须是正整数 |
| `token` | 是 | 无 | cmd-worker 使用的 OpenEvent token，必须是非空字符串 |
| `channel_ids` | 是 | 无 | worker 处理的 channel id 列表，必须是非空、无重复的正整数数组 |
| `output_dir` | 否 | `cmd-worker-output` | stdout/stderr 文件的本地根目录 |
| `max_concurrent_tasks` | 否 | `8` | 同时运行的命令任务上限，必须是正整数 |
| `default_timeout_ms` | 否 | `300000` | 请求未指定 `timeout_ms` 时的超时值，整数范围 `1..18446744073709551615`（`2**64-1`） |
| `fetch_idle_sleep_ms` | 否 | `100` | 调度循环空闲时的等待毫秒数，必须是正整数 |
| `fetch_error_max_retries` | 否 | `3` | 每次 `Fetch` 首次 RPC 异常后的最大重试次数，必须是正整数 |
| `fetch_error_retry_sleep_ms` | 否 | `1000` | `Fetch` RPC 异常后每次重试前的等待毫秒数，必须是正整数 |

未知配置字段视为配置错误。

## Channel 要求

`channel_ids` 中列出的每个 channel 必须满足[协议的通道规则](CMD_PROTOCOL_cn.md#1-通道)。
worker 启动时逐个校验可读性、协议、visibility 和 worker 成员身份；任一失败即退出。

## 运行注意事项

- `output_dir` 可以是相对路径或绝对路径；相对路径按启动 `cmd-worker` 时的当前工作目录解析。
- 启动时创建并检查 `output_dir`；无法创建或写入时，报配置/环境错误并退出。
- 输出写入 `output_dir/<channel_id>/`，不自动清理，也不限制文件大小；清理和容量保护由部署方负责。
- 并发已满时继续消费消息并在内存排队。待执行队列和恢复扫描中尚未闭合的请求没有总量上限；
  worker 不在内存中保留已完成任务的标识及请求记录。

## 任务进程生命周期

每个命令以 shell 为首进程，创建独立进程组。shell 退出后，worker 先向这个进程组发送 SIGKILL，
终止同组残留，再回收 shell 并结算任务。

自然结束后的组清理不把结果改成 KILLED；结果仍按 shell 退出码决定。已经确定的超时或 kill 原因保留，
具体状态以[运行结果协议](CMD_PROTOCOL_cn.md#5-cmdrunresult)为准。正常完成、超时、kill 和 worker
可捕获退出都使用同一清理与回收流程。

清理范围仅包括该任务创建的同一进程组。主动通过 `setsid`/`setpgid` 脱离该组的进程不在追踪范围，
worker 不提供 cgroup 或完整进程树管理。

## 恢复与退出

worker 启动时从头读取历史，以第一次 Fetch 返回的消息尾部固定本次恢复范围；随后到达的新消息
留到正常运行阶段处理。完成扫描前不执行命令，也不写回结果。

历史请求按 seq 顺序处理：

- 已有对应 result 的请求不再处理。
- run 已有 accepted 但没有 result 时，补 `RECOVERY_FAILED`，不重新执行命令。
- run 尚未 accepted 且没有 result 时，按正常请求入队，并在启动前检查是否已经超时。
- kill 或 output read 没有对应 result 时，重新处理这条请求，使用恢复时的任务状态和当时实际保留的输出文件。
  输出读取不会重建崩溃前的文件快照。

运行请求没有显式 `timeout_ms` 时，恢复处理使用当前 `default_timeout_ms`，包括补写
`RECOVERY_FAILED` 时的结果字段。请求显式给出的超时保持原值。

Fetch RPC 异常按上表重试；重试耗尽则 worker 退出。accepted/result 明确发布失败或重试后仍无法
确认写入时，也会退出。普通程序异常和停止异常直接传播，不走 RPC 重试。
这些退出路径以及 Ctrl+C、SIGTERM 都会按[任务进程生命周期](#任务进程生命周期)清理当前 worker
尚未收尾的任务并回收 shell；尚未写回结果的请求由下次启动恢复处理。
清理期间收到 SIGINT/SIGTERM 不会打断剩余任务的清理。由停止信号触发退出时，CLI 保留首次停止信号对应的退出码。

强制结束 worker 或主机故障无法保证进程清理。恢复不会终止历史 worker 遗留的 OS 进程，也不承诺命令
执行 exactly-once。
