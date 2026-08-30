# 使用版本化 Agent Skills 与受信任 Tool Registry

MediPet 使用兼容 Agent Skills 的声明式 Skill 包来保存指令、参考资源、Schema 和 Tool 绑定，并以不可变版本经历 `draft -> in_review -> published -> retired`；运行中的就诊事项固定已发布版本。可执行 Tool 独立存在于受信任 Registry，后台首期只能查看、启停和绑定预注册 Tool，含脚本的导入包进入隔离状态而不执行；以后接入 MCP、OpenAPI 或 HTTP 能力时采用独立的受管连接器发布流程，以兼顾动态配置、可复现审计和执行安全。初始部署可以保持零业务 Skill 和零业务 Tool，同时完整提供 Registry、导入导出、版本与发布 API；纯指令 Skill 可以发布，声明依赖 Tool 的 Skill 只有在绑定的受信任 Tool 可用时才能发布。
