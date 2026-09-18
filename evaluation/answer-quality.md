# 有据回答效果对照

本评测比较**同一问题、同一回答模型和提示词，接收两组实际召回资料后生成的回答**。它测量检索资料变化对回答的影响，不经过完整 Agent 路由、工具决策和预约事务，不能替代业务闭环评测，也不证明临床安全性。报告保持 `candidate`，没有自动接受基线入口。

## 固定问题与参考事实

[题集](cases/answer-quality-20260918.json)包含30题：24个单文档问题、2个跨文档复合问题、4个资料不足时应澄清或拒答的问题，覆盖当前24篇知识。医保比例、具体检验安排、实时余量和成分不明药物组合不能凭静态资料补造。健康题只考资料的范围、来源和信息处理边界，不要求诊断、开处方或个人剂量。

`question`是唯一查询文本。`source_doc_ids`、`required_facts`、`forbidden_claims`、`group`和`expected_behavior`都是评测标签，不给检索查询或回答模型。Judge另收到这些参考事实对应的完整仓库文档，避免只凭宽泛印象打分。题集目前是作者核对版本，正式运行前由评测负责人独立核对标签；运行后不得按结果改题择优。

## 召回输入

给[runner](answer_quality.py)传入已保存的实际召回快照。两个组必须恰好是`current`和`improved`，用题目ID映射，每条必须显式包含`success`和`results`。示例仅为格式，不能代替真实测量：

```json
{
  "schema_version": 1,
  "arms": {
    "current": {
      "aq01-hospital": {
        "success": true,
        "results": [
          {"chunk_id":"hospital-overview:0","doc_id":"hospital-overview","title":"医院概况","source_id":"hospital-public","source":"knowledge/hospital-overview.md","content":"实际召回原文","score":0.5}
        ],
        "metadata": {"retrieval_configuration":"实际运行配置或其证据路径"}
      }
    },
    "improved": {
      "aq01-hospital": {"success":false,"results":[],"error":"实际错误状态"}
    }
  }
}
```

这里省略的其他题会成为`retrieval_missing`，仍在分母内。成功但无结果用`success:true, results:[]`；回答模型会明确看到空证据。真实检索失败用`success:false`，不把降级伪造内容当有效证据。明确`fallback_used:true`却标成功的快照会被拒绝。改写/重排失败等细节应保存在快照中，整个原始快照会进入报告；部分召回成功与零结果不是同一状态，应在元数据注明。

回答侧只收到`question`和`evidence`。每条证据只保留实际的`chunk_id/doc_id/title/source/source_id/content`，按实际顺序附加`E1`等本次证据编号；不传检索分数、组名、案例ID或任意额外标签。编号用于逐条核对引用，不增加或重写知识事实。

## 执行方式

从仓库根目录执行；先验证、冻结输入，不调用模型：

```powershell
.venv/Scripts/python.exe -m evaluation.answer_quality --retrievals path/to/answer-retrievals.json --output evaluation/reports/answer-quality-20260918-candidate
```

负责人明确开始真实模型运行后，在相同目录和相同参数下追加`--execute`：

```powershell
.venv/Scripts/python.exe -m evaluation.answer_quality --retrievals path/to/answer-retrievals.json --output evaluation/reports/answer-quality-20260918-candidate --model deepseek-flash --execute
```

SDK读取进程已有`ANTHROPIC_API_KEY`、`ANTHROPIC_BASE_URL`、`ANTHROPIC_MODEL`和项目`MEDIPET_THINKING`配置；本命令不自动读取`.env`、不输出密钥。预备与执行的模型、thinking等配置必须一致，否则要求新候选目录。可用`--timeout`指定每次请求超时，默认120秒。

两个组均使用同一模型、同一回答/评分提示词、temperature=0、串行请求。回答上限1400 tokens，Judge上限3600 tokens，SDK重试为0。每题交替两组先后顺序以减小时间顺序偏差。30题完整两组至多60次回答和60次Judge请求；这是单次配对测量，没有多次采样或统计显著性结论。

## 评分与分母

Judge五维输出0—1及逐维依据：事实正确性、实际证据支持、需求覆盖、无依据断言控制、适当澄清/拒答。另逐个核对目标事实，列出无依据断言和实际证据原文。引用的`E`编号和逐字片段必须确实存在于本次召回内容中，否则整项记`judge_invalid`。这种校验确认引用出处，语义上的蕴含关系仍依赖Judge，需要人工抽查。

单题通过要求：五维均不低于0.8、无依据断言维度为1且无不支持断言、所有目标事实已覆盖，并满足拒答边界。不能用很高的均分抵消遗漏需求；资料不足题必须观察到适当澄清/拒答。可答题因漏召回而诚实拒答可以获得良好的证据纪律评分，但缺失的目标事实仍使需求覆盖/单题交付失败。

- 每组`total/denominator`始终30，`valid_judges`只表示有效评分数，不作为成功率分母。
- `groups.no_evidence.total`固定4，单独展示资料不足题表现；跨文档组固定2。
- `pass_rate`及`mean_scores_full_denominator`以全部计划问题为分母。检索失败、回答失败、Judge失败和未执行项都不能通过，计入全分母均值时按0处理；这是一种失败惩罚口径，不声称失败项真实语义得分就是0。
- `mean_scores_scored_only`另列有效Judge均值，只可用于诊断，不替代主指标。
- `paired.complete_pairs`/`report.complete`表示所有题的两组都有有效Judge；`processed_case_arm_pairs`用于区分已处理但失败和未处理。
- `failed_cases/status_counts`保留完整故障清单；每对题的提升与回退都保留，不只报改善样例。

## 原始证据和恢复

候选目录保存输入题集、全部参考文档及hash、实际召回快照、配置/提示词/源码指纹、逐阶段SDK原始响应/用量、回答和Judge背景/依据，以及`report.json`。报告是此独立评测的格式，不是产品`EvalReport`，不调用产品`accept_baseline`。

每个付费请求发出前先将`started`写入`requests.jsonl`并fsync，完成后写`completed`。再次运行同一候选仅复用相同输入的已完成结果；已失败不自动重试，只有started的请求标为不确定且不重新发送，防止中断后重复付费。输入、源码或配置变化必须使用新候选目录。进程互斥使用`run.lock`；硬中断遗留时需先确认原进程已退出再由负责人删除这个候选的锁，不能盲目并发运行。恢复不会移除不确定项或把它们当通过。

离线验证命令：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_answer_quality.py -q
```

这些是标签隔离、计分分母、来源引用、恢复/防重复计费和SDK参数的确定性测试，不是真实回答质量证据。
