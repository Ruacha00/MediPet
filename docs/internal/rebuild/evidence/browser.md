# 浏览器验收证据

日期：2026-09-16。工作树：`D:/Projects/Agent Learn/Project/MediPet-Rebuild`，分支 `refactor/medipet-scenario`，准备提交 `d0d2bdb` 加当前未提交实现。

## 实际业务页面

运行 `tests/browser/check_demo.py --output .scratch/V04-business`，Playwright 1.58.0 / 本机 Chromium，访问 Vite 5173，经 `/api/python` 代理至 API 8010。模型为真实 `deepseek-flash`、Anthropic 兼容入口、`MEDIPET_THINKING=disabled`；Redis 7.4.11 / Chroma 0.5.23 为真实组件。完整响应见 [browser-business.json](browser-business.json)。

结果：六组断言全部通过，页面错误 0；390×844、768×1024、1440×1000 均无横向溢出。截图保存在 ignored `.scratch/V04-business/business-*.png`。

- 儿童新事项查询明天儿科号源与准备材料，同轮返回 appointment + guidance，号源卡及材料卡来自工具结果。
- 点击号源生成 pending 方案，历史尚无确认/执行事件；双击确认只形成一条执行回执。
- 刷新后读取原患者事项，聊天查询返回同一 active 预约。
- 门诊大厅到药房返回真实 `hall-pharmacy-accessible` 无障碍路线卡及固定步骤。
- 取消先产生 pending 方案，点击确认后原预约变为 cancelled；两次业务操作各有一条回执。
- 重命名、归档、恢复、本人/家属切换及再刷新保留完整历史；归档时输入禁用，恢复后可继续。本人页面不包含儿童预约 ID。

事项 `visit-5b450d27b60e4e849e1b37e36250d7ed`，预约 `appointment-661077ff36d241658a82e0f0046cb372` 已取消；历史中的原创建回执保留当时 active 快照。

独立构建与组件测试由 U05 记录；上述浏览器使用真实模型，但不是完整评测集或已接受质量基线。

补充运行 `tests/browser/check_public_info.py --output .scratch/V04-public`：医院真实目录卡与地址/08:00—17:00 开放时间、导诊联系卡（contact_only，演示号码）、预设“现在呼吸困难”固定中断均通过，页面错误 0。完整响应见 [browser-public.json](browser-public.json)。前两次脚本把号码格式写错而中止；核对统一演示数据后修正断言，应用未改，最终三个检查通过。零模型中断的调用计数由 I04 和 V06 的隔离故障夹具补证，页面仅能确认固定响应及空工具轨迹。

## 管理页面

U05 已通过真实知识检索/导入/来源、Skill 修改重载后请求、监控和报告页面检查，详见 [management.md](management.md)。真实空输入 EvalReport 页面接线与受控失败展示均有独立证据；零样本协议检查不作质量分数，V06 继续补完整真实报告。
