# 宿主机运行开发应用并用 Compose 管理 PostgreSQL

MediPet 的标准 Windows 本地启动由根目录 `start-dev.ps1` 编排：API 与 Web 在宿主机运行，未显式配置外部数据库时只通过 `infra/compose.yaml` 启动 PostgreSQL。这个方案取代“全部应用由 Docker Compose 启动”的原设计，以牺牲完全容器化的一致性换取更直接的文件监听、快速热更新和现有 uv/pnpm 工具链复用，同时仍让开发数据库能够一条命令获得；它不决定生产部署拓扑。
