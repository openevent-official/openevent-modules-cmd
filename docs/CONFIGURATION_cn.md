# 配置

[English version](CONFIGURATION.md)

本文定义 `cmd-worker` YAML 配置结构。worker 通过启动参数读取配置：

```bash
cmd-worker --config cmd-worker.yaml
```

仓库提供了可复制的示例文件：[../examples/cmd-worker.yaml](../examples/cmd-worker.yaml)。

## 示例

```yaml
protocol: cmd.v1

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

只处理一个 channel 时，也使用单元素列表：

```yaml
channel_ids:
  - 2001
```

## 字段

| 字段 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `protocol` | 是 | 无 | 必须为 `cmd.v1` |
| `open_event.addr` | 是 | 无 | OpenEvent 服务地址 |
| `open_event.max_payload_bytes` | 否 | `16777216` | OpenEvent 部署的消息 payload 大小上限，必须是正整数；用于控制 worker 写出的 result payload 不超过部署上限 |
| `principal` | 是 | 无 | cmd-worker 使用的 OpenEvent principal，必须是正整数 |
| `token` | 是 | 无 | cmd-worker 使用的 OpenEvent token，必须是非空字符串 |
| `channel_ids` | 是 | 无 | cmd-worker 处理的 channel id 列表，必须是非空数组；每个元素必须是正整数，不能包含 `0`，不能重复 |
| `output_dir` | 否 | `cmd-worker-output` | worker 本地输出根目录；stdout/stderr 文件会持续写入该目录，清理和容量保护由部署方负责 |
| `max_concurrent_tasks` | 否 | `8` | 同时运行的命令任务上限，必须是正整数 |
| `default_timeout_ms` | 否 | `300000` | 默认命令超时，必须是正整数；请求缺失 `timeout_ms` 时使用 worker 处理该请求时的当前配置 |
| `fetch_idle_sleep_ms` | 否 | `100` | 主线程调度循环空闲时的 sleep 时间，必须是正整数 |
| `fetch_error_max_retries` | 否 | `3` | worker 任意 `Fetch` 调用首次失败后的最大重试次数，必须是正整数 |
| `fetch_error_retry_sleep_ms` | 否 | `1000` | worker 任意 `Fetch` 调用失败后每次重试前的 sleep 时间，必须是正整数 |

未知配置字段视为配置错误。

## Channel 要求

`channel_ids` 中列出的每个 channel 必须满足 `cmd.v1` 协议文档中的 channel 规则：

- channel protocol 为 `cmd.v1`。
- channel visibility 不能是 public。
- cmd-worker principal 必须是 channel 成员。
- 业务调用方 principal 必须是 channel 成员。
- channel description 必须符合 `cmd.v1` 协议文档。

worker 只处理 `channel_ids` 中列出的 channel。未列出的 channel 即使 protocol 是 `cmd.v1`，也不属于该
worker 的处理范围。

## 运行注意事项

- `output_dir` 可以是相对路径或绝对路径；相对路径按启动 `cmd-worker` 时的当前工作目录解析。
- worker 启动时会创建并写入 `output_dir`，如果目录无法创建或不可写，会报配置/环境错误并退出。
- 每个 channel 的输出会写入 `output_dir/<channel_id>/`，不同 channel 的输出文件不会混用。
- 当前实现不自动清理输出文件，也不限制单个命令的 stdout/stderr 文件大小。
