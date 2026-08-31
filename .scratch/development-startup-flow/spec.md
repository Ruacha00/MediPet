# 本地开发一键启动流程

Type: spec
Status: resolved

## 目标

让 Windows 开发者从仓库根目录运行一个命令，即可可靠地准备依赖和开发数据库、应用迁移与虚构开发 seed，并在同一终端启动和监督 MediPet API 与 Web。

## 启动合同

- `start-dev.ps1` 是标准入口，`start-dev.bat` 只负责从 CMD 转发参数。
- 显式提供 `MEDIPET_DATABASE_URL` 或 `-DatabaseUrl` 时使用外部 PostgreSQL；否则通过 `infra/compose.yaml` 启动持久化的开发 PostgreSQL。
- API 与 Web 在宿主机运行，以保留原生文件监听和快速热更新；依赖在每次启动时按 lockfile 增量同步。
- 默认应用 Alembic migrations 并执行幂等开发 seed。跳过 seed 时保留数据库现有能力状态，不做隐式停用或清理。
- API `/health` 成功且 Web 可访问即视为进程启动成功；`/ready` 单独报告。缺少或无效模型配置不会阻止存活服务启动。
- 启动前检查固定端口。冲突时报告占用进程和覆盖参数，不自动换端口或结束既有进程。
- 服务默认等待 60 秒，可由参数调整。任一应用异常退出时停止另一应用，并在一个终端显示带来源前缀的日志。
- Compose PostgreSQL 默认在应用退出后继续运行并保留命名卷；`-StopDatabaseOnExit` 只停止容器，不删除数据。
- `-OpenBrowser` 在两个应用存活后打开 Web；默认只打印地址。

## 验收

- PowerShell 启动器可被 Windows PowerShell 解析，并对相同端口、缺少命令、Node 版本、端口占用和 Docker 不可用给出明确错误。
- Compose 配置包含健康检查、固定开发凭据和持久化命名卷，且不承载 API 或 Web。
- 外部数据库路径不要求 Docker；自动数据库路径在 PostgreSQL 健康后才继续迁移。
- lockfile 更新后，统一入口不会因已有 `.venv` 或 `node_modules` 而跳过依赖同步。
- 服务启动完成前不宣告 URL 可用；readiness 失败只产生可操作警告。
- Ctrl+C、启动超时或任一子服务退出都不会遗留 API/Web 子进程。
- README、架构文档和 ADR 对标准本地启动拓扑保持一致。

## 不在本次范围

- 生产部署入口、同源生产路由或生产凭据管理。
- 非 Windows 的宿主机启动脚本。
- 自动删除、重建或降级开发数据库。
- 自动配置真实模型 Token，或接触真实患者和医院数据。
