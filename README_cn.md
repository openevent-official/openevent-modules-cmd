# OpenEvent Cmd 模块

[English version](README.md)

OpenEvent Cmd 模块提供 `cmd.v1` 协议 SDK 和 Linux 命令 worker：通过消息运行或终止命令，
把 stdout/stderr 保存到本地文件，写回结果，并支持按字节范围查询输出。

## 运行前提

- Linux，Python 3.10 或更高版本。
- 部署 Python 环境安装满足包声明的依赖，包括 `openevent-sdk>=0.8.1`。
- 已运行的 OpenEvent server，以及提前准备好的通道、token 和成员配置。
- 按[协议的通道要求](docs/CMD_PROTOCOL_cn.md#1-通道)保证可信、非 public 的通道及单 worker 部署。

本模块不管理 OpenEvent 通道、token、ACL 或部署。命令直接在 worker 机器上通过 Linux shell 执行；
不提供隔离、资源限额或审计。输出保留和容量保护由部署方负责。

## 安装和测试

```bash
make install
make test
make check-docs
```

`make install` 先构建，再安装本次唯一 wheel；同版本也替换本项目包。依赖根据 wheel 声明补齐，
已满足要求的依赖不会随项目包强制重装。使用 `PYTHON=/path/to/venv/bin/python` 选择目标环境。
只构建而不安装时运行 `make build`。

`make test` 使用选定 Python 环境已安装的 SDK，运行单元及安装回归测试，不从 SDK 子模块加载源码或生成 proto。
测试可以使用独立环境。真实服务端 E2E 尚未实现，`make e2e` 会报告此状态并返回非零。
最终产物放在 `dist/`，临时文件、缓存和测试数据放在 `build/`。

## 运行 worker

复制并按部署环境修改 [examples/cmd-worker.yaml](examples/cmd-worker.yaml)，然后启动：

```bash
cmd-worker --config cmd-worker.yaml
```

worker 只处理配置的 `channel_ids`。启动时验证配置、SDK 和通道环境；配置格式见
[配置文档](docs/CONFIGURATION_cn.md)。

## 文档

- [协议](docs/CMD_PROTOCOL_cn.md)：消息字段、任务状态、输出编码和通道要求的权威规格。
- [配置与运行说明](docs/CONFIGURATION_cn.md)：配置字段、默认值、任务进程生命周期、启动恢复和退出契约。
- [SDK 用法](docs/SDK_USAGE_cn.md)：Python 发布、解析和错误处理接口。

## 运行边界

worker 在 shell 退出后清理同组残留，再回收 shell 并结算；管理范围见
[任务进程生命周期](docs/CONFIGURATION_cn.md#任务进程生命周期)。启动恢复和致命通信失败时的行为见
[恢复与退出](docs/CONFIGURATION_cn.md#恢复与退出)。
并发已满时继续消费消息并在内存排队；队列和历史结果索引没有总量上限。

## 包内容

- `openevent.cmd_sdk`：`cmd.v1` payload 编解码、校验、发布和解析辅助。
- `openevent.cmd_worker`：配置、通道校验、命令调度、执行和结果写回。
