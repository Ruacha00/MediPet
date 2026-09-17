# U002-09 有界准备检查点

日期：2026-09-17。05 已完成；主 agent 授权准备环境/脚本，但 08 尚未交回，09 保持 todo，不开展正式模型或业务验收。

## 已准备

- 独立 Compose 项目 `medipet-u002-validation`，网络同名前缀。仅启动新 Redis 与 Chroma，均已 healthy；分别绑定宿主 26379/28001。新卷 `medipet-u002-validation_redis-data`、`medipet-u002-validation_chroma-data` 与原两套卷无关联。
- API 28000、前端 28088 预留但尚未启动；原项目 API/前端仍停止。固定隔离演示 Redis 密码 `medipet-u002-demo` 仅为本地合成验收配置，不来自 `.env`。
- [run_browser_acceptance.py](run_browser_acceptance.py) 复用已有 demo/health/evaluation 浏览器脚本，保留原业务检查；新增模型/校准源码指纹、实际容器 baseline 前后摘要、`intent_embedding` 状态分布和非语义样本单列。评测按 conv_id+turn 去除质量行/业务行重复，不把行数当准确率。
- 不传 `--execute` 只检查固定清单及校准是否存在，不联网、不读 `.env`、不调模型。传 `--execute` 仍要求协调人先确认 08 冻结和新容器部署；每次输出目录必须全新，不自动重试或接受 candidate。

## 后续正式派发需要

1. 08 给出固定校准、模型/模板/数据指纹及 07 执行器状态。root 确认冻结，重新构建此隔离项目的 API/前端，不能使用 05 早期业务源码镜像当最终镜像。
2. `tests/integration/test_semantic_intent_runtime.py` 待正式 09 接取后补真实模型共享/急症零调用/故障与恢复的确定性集成验证；此前不抢跑下游验收。
3. 同一隔离项目完成现有 demo 与 health 浏览器链路，然后真实完整 candidate 只跑一次；输出保留完整失败。确认 `/health` 为 semantic_ready，页面和API模型空间相同。
4. 后端/前端完整回归由 root 协调一次运行，避免重复重负载。既有基线不自动接受，不删/重置任一旧卷。

示例准备命令（无真实调用）：

```powershell
.venv\Scripts\python.exe docs/internal/updates/U002-semantic-intent/evidence/runtime/run_browser_acceptance.py --mode demo --output evaluation/reports/semantic-intent-u002/browser-demo
```

正式浏览器需使用包含 Playwright、Pillow、reportlab 的已准备 Python（当前包工具运行环境可复用），再由 root 明确通知追加 `--execute`。缺少依赖先报告，不在生产镜像临时安装浏览器工具。

启动现有隔离存储时使用 `docker compose --project-name medipet-u002-validation --env-file .env.example up -d redis chromadb`，并在该命令进程内显式设置上述端口和演示密码；后续启动 API 需由 root 接入授权的本地聊天配置，本次未读取真实 `.env`。
