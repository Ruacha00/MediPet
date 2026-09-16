# 浏览器验收

先按根 README 启动应用，配置可用的测试模型。脚本用实际浏览器访问应用；业务脚本会创建一条演示预约并取消，完整历史保留。请使用演示数据环境。

浏览器工具单独安装，不是应用运行依赖：

```powershell
python -m pip install playwright==1.58.0
python -m playwright install chromium
python tests/browser/check_demo.py --url http://127.0.0.1:8088 --output .scratch/browser
python tests/browser/check_public_info.py --url http://127.0.0.1:8088 --output .scratch/browser-public
```

已验证的开发地址为 `http://127.0.0.1:5173`，同源代理连接 API 8010。正式启动入口仍是根 README 的 Compose。

`check_demo.py` 检查号源与准备材料协作、选号待确认、双击确认、记录查询、无障碍文字指引、取消、刷新、归档恢复和患者切换；在 390、768、1440 像素宽度检查溢出并截图。`business.json` 保存实际 API 响应、业务 ID、断言和页面错误，失败另存截图。完整消息不能单凭质量评分判定通过。

`check_public_info.py` 检查医院地址/开放时间、人工导诊联系卡和预设急症固定响应；零模型调用保证还需结合接口及隔离故障测试。

管理页检查与 Skill 临时修改需要独占其重载时段，不能与正式模型评测并发；具体操作和清理以 `check_management.py --help` 及内部浏览器证据为准。
