# Docker 沙箱验收

2026-10-15（Asia/Shanghai）在 Windows 11、WSL 2.6.1、Docker Desktop 4.95.0 / Docker Engine 29.8.2、Linux cgroup v2 上验证。Python 为 3.13.7，基础镜像为本地 `python:3.12-slim`。运行真实 Windows Job/CLI 执行链，未使用替代的 Linux subprocess 执行器。

v0.5.5 复验结果：全量 **280 passed**；Docker 专项为 **51 项离线回归 + 16 项真实容器测试**。普通测试默认不要求 Docker，显式跳过 16 项实机测试。真实测试不调用外部模型，不使用用户凭据。

## 实机覆盖

| 范围 | 验证方式 |
|---|---|
| UID/GID 与权限 | 容器内检查 UID/GID 为 65534、CapEff 为 0、NoNewPrivs 为 1 |
| 只读根与临时目录 | 检查真实根挂载为 ro；验证可写 `/tmp` 与 nosuid/nodev 配置 |
| 禁网络 | 检查 network=none，并从容器发起连接，确认不能访问外网 |
| 资源约束 | 核对 Docker 配置及实际 cgroup：512 MiB 内存/交换上限、1 CPU、64 PID |
| 内存限制 | 分配超过容器上限的内存，确认 OOMKilled 与退出码 137，并检查资源移除 |
| PID 限制 | 在容器内 fork 至限制，确认 EAGAIN；不影响宿主进程空间 |
| 文件过滤 | `.env*`、私钥、`.ssh`、`.git` 不进入挂载；宿主环境秘密不透传 |
| 项目副本 | 中文/空格路径、嵌套 cwd 可用；容器内修改、删除、新增均不写回项目 |
| 镜像元数据 | 测试镜像故意指定 ENTRYPOINT、HEALTHCHECK 和匿名卷；确认只执行指定命令、健康检查被禁用 |
| 回执与输出 | 非零容器退出码、stdout/stderr 实时回调、双通道大输出排空且各限 32000 字节 |
| 生命周期 | 正常退出带子进程、取消、超时后，独立查询确认容器、匿名卷及临时副本已移除 |
| 审批与运行时 | 真实工具轮次覆盖批准、拒绝、审批后修改项目/参数；校验回执配对及宿主编辑指标 |
| GUI 入口 | 在模型设置中选择 Docker，再切换默认或完全访问，分别验证 GUI 审批/自动执行、Docker 回执及宿主文件不变 |
| 缺失镜像 | 本地镜像不存在时拒绝，不触发拉取或宿主执行 |

测试为每次运行构建一个独有名称的轻量镜像，继承已准备好的 Python 基础镜像，只增加配置。结束时移除该测试镜像标签及本次创建的资源，不运行 prune，也不清理其他用户容器、镜像或卷。基础镜像保留供后续使用。

## 故障回归及修复

`tests/unit/test_docker.py` 在无需 Docker 的情况下覆盖预检失败、非法镜像 ID、执行时镜像与审批不一致、项目增删改导致审批失效、非法 cwd、非有限或非正超时、预先取消、复制期间取消、创建/启动/查询异常、损坏或不完整的容器状态、清理连接异常及错误对象的不存在提示。

修复后的执行器不会用 `docker start` 本身的退出码替代未知容器结果：只有明确的 exited 状态与整数 ExitCode 才返回完成回执。清理使用独立的取消事件；即使 rm 调用异常，也继续验证指定容器是否确已不存在。大小写不同的 Docker 诊断文本兼容处理，但资源类型和完整容器名必须匹配。

## 复测

先启动 Docker Desktop 并选择 Linux 引擎。本项目不在普通命令执行时自动安装 Docker 或拉取镜像。首次测试需显式准备镜像：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File packaging/test_docker.ps1 -PullImage
```

已有本地镜像时：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File packaging/test_docker.ps1
```

脚本会查找 PATH 及 Docker Desktop 的当前用户/全用户默认 CLI 目录。开启实机验证后，Docker 未安装、引擎未启动、不是 Linux 后端或本地镜像缺失均视为失败。可用 `-Image` 指定预备的 Python 镜像、`-Python` 指定开发解释器、`-Report "$env:TEMP\minicodex-docker.xml"` 保存 JUnit 报告；默认不在项目目录生成报告或测试缓存。

全量实机验证：

```powershell
$env:MINICODEX_DOCKER_TESTS = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PATH = "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin;" + $env:PATH
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
Remove-Item Env:MINICODEX_DOCKER_TESTS
```

常规 GitHub Windows CI 运行离线回归，默认跳过实机组，不将 hosted runner 当作已配置 Docker Desktop 的验收机。实机测试要求 Linux cgroup v2；其他 Docker Desktop/Engine 版本、cgroup v1、非 Windows 客户端、恶意内核或 daemon 漏洞不在本次验收范围。执行器修复随 v0.5.4 发布，GUI 执行环境入口随 v0.5.5 发布。

参考：[Docker Desktop Windows 安装](https://docs.docker.com/desktop/setup/install/windows-install/)、[Docker create](https://docs.docker.com/reference/cli/docker/container/create/)、[容器资源限制](https://docs.docker.com/engine/containers/resource_constraints/)。
