# U003 组件接线契约

- `MessageContent.vue`：只读 `content: string` prop，无事件；仅渲染文字与受控Markdown，禁止HTML、危险协议、远程图片。root负责App接入。
- `PatientVisitPanel`、`ReportUpload`：保留现有props/events名称与载荷；局部样式由组件独占。抽屉与全局焦点由root实现。上传仍emit upload(File)。
- `BusinessArtifacts`、`HealthArtifacts`：现有props/events不变；错误仅由root传入当前关联操作，历史卡片不得重复显示全局错误。卡片内ID/details可折叠，服务端状态/来源/显式确认保留。
- App/global styles由root独占；任何需要新增props先明确交回，不并发修改共享文件。测试按各自新文件隔离。
- 03独占package.json与锁文件，其余任务不改依赖；现有app-integration测试由root接线。
- 现有端点、identity checks、workspaceEpoch、防重pendingOperations继续使用。未完成发送是临时界面状态，不伪造持久化历史。
