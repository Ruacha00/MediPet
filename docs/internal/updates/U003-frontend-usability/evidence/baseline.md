# U003-01 基准与边界

基准提交e81f447，实施分支codex/u003-frontend-usability。前端源码尚无功能改动时记录。

源码与浏览器确认：1个无点击动作的头像按钮；顶栏3个同级视图；首页onMounted会读取health、knowledge stats、monitor和skills；回复以普通p插值输出，Markdown标记可见；左右内部滚动；readHistory每次滚底；ReportUpload文件input隐藏且无可聚焦触发控件。

现有常用路径：选患者→新建/选择事项→输入查询→选号源→待确认方案→显式确认→回执；取消另需准备方案与确认。优化不取消确认步骤。

已只读观察当前桌面浏览器，未修改用户事项。该页面包含历史对话，其部署版本不据此推断为最新。360/768/1440目标尺寸的自动对照在独立开发预览验证，不改变用户现有服务。

GitNexus绑定当前绝对工作树，索引e81f447。readHistory上游16符号/15相关函数，风险CRITICAL；发送链含选号与取消准备，确认与切换的Vue事件索引为UNKNOWN/不完整。已检查App模板emit接线及调用点，保留checkIdentity/workspaceEpoch、完整权威历史、防重和确认接口。
