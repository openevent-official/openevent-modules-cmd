# OpenEvent Cmd 模块

[English version](README.md)

OpenEvent Cmd 模块提供 `cmd.v4` 协议 SDK 和 Linux 命令 worker：通过消息运行或终止命令，
把 stdout/stderr 保存到本地文件，写回结果，并支持按字节范围查询输出。

## 运行前提

- Linux，Python 3.10 或更高版本。
- Python 环境已安装 `openevent-sdk>=0.13.0`。
- 已运行的 OpenEvent server，已配置通道、token 和成员；通道须满足[协议要求](docs/CMD_PROTOCOL_cn.md#1-通道)。

命令直接在 worker 机器上通过 Linux shell 执行，不提供隔离、资源限额或审计。

## 构建和安装

在项目根目录运行 Make。先激活一个可写且已安装 pip 的虚拟环境，Make 默认使用其中的 `python3`。也可以用 `PYTHON` 直接指定虚拟环境的解释器。首次使用时可按下面的示例创建虚拟环境并安装 Cmd。

```bash
python3 -B -m venv build/venv
source build/venv/bin/activate
make install
```

| 命令 | 用途 |
| --- | --- |
| `make build` | 默认目标。按 `pyproject.toml` 在隔离环境中构建当前源码，成功后将本次 wheel 移入 `dist/`。 |
| `make install` | 将本次 wheel 装入所选环境。同版本 Cmd 也会替换，已满足声明的依赖继续使用。 |
| `make test` | 安装本次 wheel 和测试依赖，用同一个 Python 运行普通测试。可用 `TEST_ARGS=-q` 等 pytest 参数。 |
| `make clean` | 清理 Cmd 的发布包、缓存和测试临时文件，保留虚拟环境、离线依赖和其他项目的产物。 |

在同一工作目录中顺序执行这些命令。所选虚拟环境决定安装位置。`make install` 和 `make test` 会在构建前检查 pip 的环境变量和配置文件。如果设置了安装前缀、启用了用户目录安装或指定了其他安装位置，命令会指出冲突的设置。

安装测试会创建临时虚拟环境并复用基础解释器的 pip，因此基础解释器也须装有 pip。测试临时文件在 `build/unit/` 下。

离线时，在 `build/wheelhouse/` 备齐与目标 Python 和平台匹配的构建、运行依赖（包括 OpenEvent SDK）及其间接依赖。要运行测试，还须备齐测试依赖。以下命令从该目录安装。

```bash
PIP_NO_INDEX=1 PIP_FIND_LINKS=build/wheelhouse make install
```

把示例中的 `install` 换成 `test` 即可离线测试。

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
