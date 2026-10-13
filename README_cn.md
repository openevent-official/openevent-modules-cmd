# OpenEvent Cmd 模块

[English version](README.md)

OpenEvent Cmd 模块提供 `cmd.v3` 协议 SDK 和 Linux 命令 worker：通过消息运行或终止命令，
把 stdout/stderr 保存到本地文件，写回结果，并支持按字节范围查询输出。

## 运行前提

- Linux，Python 3.10 或更高版本。
- Python 环境已安装 `openevent-sdk>=0.11.1`。
- 已运行的 OpenEvent server，已配置通道、token 和成员；通道须满足[协议要求](docs/CMD_PROTOCOL_cn.md#1-通道)。

命令直接在 worker 机器上通过 Linux shell 执行，不提供隔离、资源限额或审计。

## 安装和测试

```bash
make install
make test
make check-docs
```

用 `PYTHON=/path/to/venv/bin/python` 选择安装和测试环境。`make install` 构建并替换本项目包，
版本相同也会替换；只补齐缺失或版本不符的依赖。只构建时运行 `make build`。
`make install` 和 `make e2e` 均可通过 `INSTALL_ARGS=--user` 使用用户目录安装。

`make test` 运行单元及安装回归测试。端到端测试需要本次成功构建的 OpenEvent server：

```bash
OPENEVENT_SERVER_BIN=/path/to/openevent_server make e2e
```

`make e2e` 将当前 Cmd 包构建并安装到选定环境，再启动真实服务端和 worker。两类测试均使用该环境
已安装的 SDK，可通过 `TEST_ARGS` 传递 unittest 参数。端到端用例数据和日志位于 `build/e2e/cases/`。
最终产物放在 `dist/`，临时文件、缓存和测试数据放在 `build/`。

## 运行 worker

复制并按部署环境修改 [examples/cmd-worker.yaml](examples/cmd-worker.yaml)，然后启动：

```bash
cmd-worker --config cmd-worker.yaml
```

worker 只处理配置的 `channel_ids`，字段和运行行为见[配置文档](docs/CONFIGURATION_cn.md)。

## 文档

- [协议](docs/CMD_PROTOCOL_cn.md)：消息字段、任务状态、输出编码和通道要求。
- [配置与运行说明](docs/CONFIGURATION_cn.md)：配置、进程管理、恢复和退出。
- [SDK 用法](docs/SDK_USAGE_cn.md)：Python 发布、解析和错误处理接口。
