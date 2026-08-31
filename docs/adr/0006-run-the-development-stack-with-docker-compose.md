# 用 Docker Compose 运行完整开发栈

MediPet 的标准 Windows 本地启动改为根目录 `start.bat` 或等价的 `docker compose up --build`：PostgreSQL、FastAPI API 与 Next.js Web 分别运行在 Compose 服务中，API 自动执行 migration 与幂等开发 seed，源码挂载保留热更新，DeepSeek 配置以运行时 secret 提供。该决定取代 ADR 0005 的宿主机 API/Web 启动方案，删除原 PowerShell/批处理编排器，以只要求 Docker Desktop 为代价接受首次镜像构建时间；它仍不决定生产部署拓扑。
