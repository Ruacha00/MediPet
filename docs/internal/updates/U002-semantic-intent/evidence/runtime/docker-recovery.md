# Docker Desktop 恢复记录

日期：2026-09-17，时间均为 Asia/Shanghai。Docker Desktop 4.84.0.234817；Windows 25H2，build 26200.9457。

## 原始故障与判断

12:42—12:45 的 `AppData/Local/Docker/log/host/com.docker.backend.exe.log` 记录 Inference Manager 无法移除 `AppData/Local/Docker/run/dockerInference`，错误为 `The file cannot be accessed by the system`，listener 同时报文件名/目录名语法错误。故障后没有 Docker 后台进程；该路径具有 ReparsePoint 属性，`fsutil reparsepoint query` 返回 Error 1920。

Docker 官方问题库的 [#527](https://github.com/docker/desktop-feedback/issues/527) 报告了相同路径、错误和 Windows build 条件，并提到关闭模型功能不能保证绕开监听器。它是用户报告，不是 Docker 已确认的根因。运行恢复后同类活动 socket 的 `fsutil` 查询仍返回 1920，因此不能仅凭此命令认定 NTFS 损坏。

## Agent 实际操作

确认 `C:/Users/RuaCha/AppData/Local/Docker/run` 是普通目录、没有 Docker 进程、源和目标均在同一 Docker 父目录后，于 12:47:34 将其原样保留改名为 `run.before-inference-repair-20260917-124734`。其中只有三个临时 socket 名称；没有遍历删除。随后只执行一次隐藏启动 Docker Desktop。

12:47:38 启动越过原 Inference 错误，但发生 Secrets Engine 的 `AppData/Local/docker-secrets-engine/engine.sock` 同类错误。Agent 仅检查目录元数据，未改名、删除此目录，未停止进程或重复启动。

## 用户操作与归因限制

12:47:48 日志明确记录错误窗口 `actionButtonClicked`，context 为 `Reset to factory defaults`，随后 `application reset complete`；用户之后明确确认自己点击了该按钮。Agent 没有调用或点击 reset，也没有修改设置、删除 WSL 发行版或提交外部诊断。

12:47:59 新进程启动，12:48:07 日志显示 `engine linux/wsl state starting -> running`。最终恢复发生在**用户重置之后**，不能归因为 Agent 单独的临时目录改名；更不能声称重置没有影响数据。

## 恢复后只读检查

`docker version` 成功：Engine 29.6.2、Linux/amd64、WSL2 kernel 6.18.33.2。`docker ps -a` 能列出 11 个原容器；两套 MediPet 的 API/前端/Redis/Chroma 共 8 个容器都处于 `Exited (255)`。未启动旧 API 或任一既有 Compose 服务。

`docker volume ls` 能列出 `medipet-rebuild_{api-data,chroma-data,redis-data}` 与 `medipet-validation_{api-data,chroma-data,redis-data}` 六个命名卷。这里只确认容器与卷对象存在，**没有读取/比对业务内容，不能证明内容与重置前完全相同**。

本次留存的 runtime 目录备份未清理。后续 U002 模型构建使用独立镜像和一次性验证容器，禁止借验收删除或重置上述卷。
