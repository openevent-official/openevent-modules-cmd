# OpenEvent Cmd 模块

OpenEvent Cmd 模块提供 `cmd.v1` 协议 SDK 和 Linux 命令 worker。调用方向
OpenEvent 写入命令请求，worker 将每个请求作为异步 subprocess 执行，把 stdout/stderr
写入文件，把最终结果写回同一个 channel，并支持通过消息按 offset 局部查询输出文件内容。

## 运行前提

- Python 3.10 或更高版本。
- 当前 Python 环境中可解析的 `openevent-sdk>=0.3.0`。
- 已运行的 OpenEvent server。
- 已提前创建 `protocol="cmd.v1"` 的非 public channel。
- cmd-worker principal 和业务调用方 principal 都必须是目标 channel 成员。

本项目不创建 OpenEvent channel，不管理 token、成员、ACL 或部署。

## 安装和测试

```bash
make test
make build
make install
```

`make test` 使用当前 Python 环境中已经安装好的 `openevent-sdk>=0.3.0`，不会从 SDK 子模块安装或生成 SDK。
构建产物写入 `dist/`，临时文件写入 `build/`。

## 安全边界

cmd worker 会在 worker 所在机器上通过 Linux shell 执行请求里的 `command` 字符串。请只把 worker 接入可信、
非 public 的 OpenEvent channel。

当前实现不提供 shell 沙箱、容器隔离、用户切换、CPU/内存/进程数限制、文件系统隔离、审计策略或输出文件自动清理。
stdout/stderr 会持续写入本地 `output_dir`，磁盘容量和清理策略由部署方负责。多 worker 同时消费同一个
cmd channel 的冲突也不由当前协议处理，部署时应保证同一 channel 同一时刻只有一个有效 cmd worker。

## 运行 worker

复制并按部署环境修改 [examples/cmd-worker.yaml](examples/cmd-worker.yaml)，然后启动 worker：

```bash
cmd-worker --config cmd-worker.yaml
```

配置格式见 [docs/CONFIGURATION.md](docs/CONFIGURATION.md)。OpenEvent channel、token、成员和 ACL 需要提前准备。

## 文档

- [docs/CMD_PROTOCOL.md](docs/CMD_PROTOCOL.md)：`cmd.v1` 事件协议。
- [docs/CONFIGURATION.md](docs/CONFIGURATION.md)：worker 配置。
- [docs/SDK_USAGE.md](docs/SDK_USAGE.md)：Python SDK 用法。

## 当前能力和边界

- 协议版本：`cmd.v1`
- Python 版本：`>=3.10`
- OpenEvent SDK：`openevent-sdk>=0.3.0`
- 默认命令超时：`300000` 毫秒
- 默认并发上限：`8`
- worker 配置必须显式指定 `channel_ids`；worker 只处理这些 channel 中的消息
- 配置中的 channel 必须是 `cmd.v1`、非 public、且 cmd-worker principal 是成员；public channel 会导致
  worker 启动失败并退出
- worker 使用 OpenEvent `Fetch(only_my_recipient=false)` 拉取消息；当前 SDK 支持 `channels` 参数时会传入
  配置的 `channel_ids`，否则在本地过滤 channel
- 命令执行方式：`command` 字符串，由 Linux shell 执行
- 命令工作目录：`workdir` 可选；如果出现，必须是绝对路径
- 命令请求不支持独立的 `env` 或 `stdin` 字段
- stdout/stderr 文件路径：`<channel_id>/<cmd.run.request seq>.stdout` 和
  `<channel_id>/<cmd.run.request seq>.stderr`
- `cmd.run.result` 输出规则：stdout/stderr 大于 1024 字节只写文件元信息，小于等于
  1024 字节直接写内容
- 输出内容能按 UTF-8 解码时 `content_encoding=utf-8`，否则 `content_encoding=base64`
- 输出局部查询：`cmd.output.read.request(target_seq, stream, offset, nbytes)`，
  结果通过 `cmd.output.read.result.content` 返回；worker 可为保证 result payload 不超过
  OpenEvent 部署上限而少于 `nbytes` 返回
- 同一 channel 内成员都可以读取该 channel 内的命令输出
- 执行并发满时 worker 继续消费新消息，并在内存中维护不设长度上限的待执行队列
- kill 命中待执行队列中的未启动任务时，worker 从队列删除该任务并写 `cmd.kill.result` 和 `cmd.run.result`
- worker 启动从头消费；只有已有 `cmd.run.accepted` 但没有 `cmd.run.result` 的历史 run request
  补写失败 result，没有 accepted 的 run request 正常处理
- 能识别请求类型的非法 cmd payload 在对应 result 中写 `status=REJECTED` 和 `error_message`；
  损坏到无法识别请求类型的 payload 只记录并忽略
- 同一 channel 内成员都可以 kill 该 channel 内运行中或待执行的任务

## 包内容

- `openevent.cmd_sdk`：`cmd.v1` payload 编解码、校验、发布和解析辅助。
- `openevent.cmd_worker`：`cmd-worker` 配置、channel 校验、命令执行和结果写回。
