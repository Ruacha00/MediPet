# DeepSeek 短 JSON 请求验证

日期：2026-09-16。范围：验证 `MCPToolManager.rewrite_query` 的 JSON 解析失败是否来自默认 thinking 占用原输出预算，并接入一个可选请求配置。

## 输入与实际结果

只执行两次真实 Anthropic SDK 调用，设置 `max_retries=0`。密钥由本工作树 `.env` 经 `load_dotenv(..., override=True)` 加载，没有打印或保存。先用捕获客户端执行当前 `rewrite_query` 取得原样 prompt 与参数，再将完全相同的请求分别以默认配置及 `extra_body={"thinking":{"type":"disabled"}}` 发出。

两次均使用 `deepseek-flash`、`max_tokens=256`、`temperature=0.3`；请求 prompt 的 SHA-256 为 `3d0f69935e6f67de093a27813008591702c6397ea8b74c25b442d9789116a481`。原始查询为“查一下明天儿科的号，顺便告诉我要带什么。”

| 参数 | 返回块类型 | stop_reason | 正文 | 输入 / 输出 token |
| --- | --- | --- | --- | --- |
| 默认 | thinking | max_tokens | 空 | 126 / 256 |
| disabled | text | end_turn | 可解析的三项搜索子查询 JSON 数组 | 100 / 33 |

该对照直接复现默认 thinking 耗尽短预算、没有可解析正文的情形；关闭 thinking 后同一原始业务提示完整返回。输入 token 数的变化是服务返回的计数，原提示文本及请求预算未变。详细响应只保留正文、块类型、停止原因和 token 计数，见 [脱敏探针数据](deepseek-thinking-probe.json)，没有保存 thinking 正文。

官方说明确认 thinking 默认开启；Anthropic 兼容接口支持 thinking 字段。此次使用的显式关闭值符合官方开关定义。[Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)、[Anthropic API compatibility](https://api-docs.deepseek.com/guides/anthropic_api/)。

## 实现与影响分析

- `core/llm_utils.py` 新增 `llm_request_options()`：`MEDIPET_THINKING` 未设置或为空时返回空参数；`disabled` 返回上述 SDK `extra_body`。其它非空值报配置错误；不自动猜测供应商或启用方式。
- 统一接入 9 处既有请求：Agent 工具循环、结果整合、意图识别、画像提取、记忆压缩、摘要合并、查询改写、检索重排、质量 Judge。原 token/温度、提示词、轮数、投票与业务算法保持不变，`extract_text_content` 仍只读取文本块。
- `.env.example` 示例配置为 `MEDIPET_THINKING=disabled`。本子任务未修改真实 `.env`，应用重启及实际环境启用由主任务执行。
- GitNexus 明确指向 `D:/Projects/Agent Learn/Project/MediPet-Rebuild`，索引时间 `2026-09-16T07:47:37.679Z`，HEAD `d0d2bdb`，2639 nodes / 6176 edges / 196 flows。意图识别方法 HIGH；共享文本工具原调用链 CRITICAL（9 个直接调用、22 个三层关联符号），修改前已说明范围。公共提取函数保持不变，仅同模块新增 helper。
- `update_profile` 为 UNKNOWN，已核对 API 背景任务引用；`judge` 为 UNKNOWN / lower-bound，已核对 evaluator `_turn` 的注入调用。其余受改调用方法 LOW，直接路径均按当前源码补核。FTS 故障和流程提取上限未被当作完整搜索依据；本子任务没有重建索引或提交。

## 验证

- 新增 `tests/test_llm_request_options.py`：**7 passed**。包括空配置、显式关闭、独立参数字典、非法值、文本提取保留行为，以及全部 9 条请求路径在 unset/disabled 下的预算、温度、消息和工具参数。
- 对 Agent、意图、记忆、RAG/Skills、评测器运行关联假模型回归：**223 passed, 14 deselected**（`-k 'not real'`），证据 `.scratch/deepseek-thinking-regression.xml`。真实存储参数组以及名称含 real 的用例未纳入这轮筛选；此前 V02 的完整 20 项验证保持独立记录。
- 六个修改的生产模块编译通过；脱敏 JSON 读取及 disabled 三项数组解析检查通过。除上述两次探针外，没有新增真实模型调用。
- 结论仅覆盖这一 SDK 参数与短 JSON 输出故障。预约“准备”措辞误触发多角色等路由问题由主任务独立核查，不用本结果替代路由或最终实际模型验收。
