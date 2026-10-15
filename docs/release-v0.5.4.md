# MiniCodex v0.5.4

发布日期：2026-10-15（Asia/Shanghai）。本版合并 Docker 沙箱测试与执行器修复，并按后端/前端分离后的目录重新构建 Windows 程序包。

## 变化

- Docker 执行前核对审批时固定的镜像 ID，项目或参数变化使审批失效。
- 容器退出状态缺失、仍在运行或有启动错误时，不再误报命令成功。
- 清理使用独立的取消事件；rm 调用异常后仍查询指定容器，仅在明确确认该对象不存在时完成。
- 拒绝非正或非有限超时，复制项目期间取消也不会继续创建容器。
- 禁用镜像健康检查，`/tmp` 增加 nodev；继续使用非 root、禁网络、只读根及 CPU/内存/PID 限制。
- Docker 测试包含 51 项故障回归与 14 项真实 Windows/Docker Desktop 容器测试；加入可复用的 PowerShell 验收脚本。
- Windows CI 使用 UTF-8，修复中文日志在非中文系统下导致程序包自检失败的问题。

## 下载与升级

- `MiniCodex-0.5.4-Setup.exe`：Windows 11 x64 当前用户安装器。
- `MiniCodex-0.5.4-Windows-x64.zip`：便携目录包；解压后保留完整 MiniCodex 目录及 `_internal`，双击 MiniCodex.exe。
- `SHA256SUMS.txt`：安装器和便携包的 SHA-256 校验值。

升级前关闭客户端。继续使用同一产品标识与默认数据目录，已有会话、普通设置及 Windows 凭据沿用；默认卸载保留用户数据。客户端运行不需要安装 Python，项目自身的 Python/Git 工具链仍需另行准备。程序尚未代码签名。

Docker 需另行安装 Docker Desktop、启动 Linux 引擎并准备本地镜像。客户端不会自动拉取镜像，容器中的修改仅影响临时副本。该版本不增加新的 Docker 界面入口；现有权限菜单与界面布局保持既有行为。

## 验证与复测

完整验收范围见 [Docker 沙箱验收](docker-sandbox.md)。开启实机组时全量 260 项测试通过；常规测试不要求 Docker，运行 246 项并跳过 14 项实机测试。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File packaging/test_docker.ps1
```

冻结程序的离线启动、兼容 HTTP、审批→补丁→原生测试流程均已通过，并核对运行版本为 0.5.4。独立测试安装器的安装、覆盖重装、卸载、100%/150%/200% 缩放、会话与凭据保留及测试资源清理均已通过。本机验收不等于干净 Windows 虚拟机或每个历史版本升级组合均已验证。
