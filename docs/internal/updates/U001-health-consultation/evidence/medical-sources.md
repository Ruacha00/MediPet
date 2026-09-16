# U001 医疗信息来源与实施边界

核对日期：2026-09-16。仅使用官方公开页面；从原文归纳中文说明，不导入图片、完整文章或未经验证的模型生成医学事实。

| source_id | 官方页面与本次核对内容 | 当前资料边界 |
| --- | --- | --- |
| nhs-common-cold | [NHS Common cold](https://www.nhs.uk/conditions/common-cold/)：一般表现、病毒/抗生素区分与就医提示 | 常见病FAQ，不据症状确诊 |
| nhs-conjunctivitis | [NHS Conjunctivitis](https://www.nhs.uk/conditions/conjunctivitis/)：眼部表现、卫生与需及时评估的眼痛/视力变化 | 有限眼部方向，不诊断结膜炎或推荐滴眼液 |
| nhs-child-fever | [NHS High temperature in children](https://www.nhs.uk/symptoms/fever-in-children/)：年龄相关发热就医提示、儿童给药边界 | 不把39.5℃当所有年龄统一医学阈值，不换算儿童剂量 |
| nhs-stomach-ache | [NHS Stomach ache](https://www.nhs.uk/symptoms/stomach-ache/)：腹部不适需要评估及急症警示 | 不诊断胃病，不安排目录外科室 |
| dailymed-acetaminophen-500 | [DailyMed Acetaminophen Tablets 500 mg](https://dailymed.nlm.nih.gov/dailymed/fda/fdaDrugXsl.cfm?setid=983debdf-4a61-4dde-ba34-567fdd17720e&type=display)，Preferred Pharmaceuticals，NDC 68788-4132，effective time 20260611/version 1 | 指定美国500 mg普通口服片；标签成人/12岁及以上数字不转换为个人剂量；不覆盖复方/缓释/儿童液体 |
| dailymed-ibuprofen-200 | [DailyMed Ibuprofen Tablets USP 200 mg](https://dailymed.nlm.nih.gov/dailymed/fda/fdaDrugXsl.cfm?setid=4d830b9b-b396-9581-7572-c7f19ff17597&type=display)，Dr. Reddy's，NDC 75907-428，effective time 20260701/version 1 | 指定美国200 mg普通包衣片；不映射缓释胶囊、混悬液、其他规格或含糊品牌 |
| medlineplus-lab-results | [MedlinePlus How to Understand Your Lab Results](https://medlineplus.gov/lab-tests/how-to-understand-your-lab-results/)：参考区间、单位、阳性阴性与不确定性 | 仅通用术语；报告预处理与上传由独立任务验收 |

`health/data/sources.json` 保留可返回给前端的来源；`medications.json` 保存两个明确产品的标签摘要。`reviewed_at` 指本次人工工具核对日期，不声称医学专家审查、药品批准日期或自动更新。

分诊的症状到 `dep_internal / dep_pediatrics / dep_ophthalmology` 映射是本项目在三个现有虚构医院科室中的演示规则；NHS资料支撑症状及就医边界，并未批准这些映射。规则不输出疾病结论，资料不足澄清，紧急表现不返回普通预约科室。共享 `core.emergency.detect_emergency` 由U001急症任务维护；独立调用分诊也复用它。高烧超过39.5℃是用户要求的演示中断规则，不是已验证的通用诊断阈值。

药品入口精确匹配名称/已收录规格别名，不用裸子串匹配混淆剂型。标签已明确的重复成分、华法林、NSAID/阿司匹林/萘普生、抗凝或类固醇警示可返回；未收录组合（包括没有明确收录的两个已知药之间的组合）不能被标为安全。标签信息不能直接替代不同地区、厂家或剂型的说明书。个人适用性、儿童、孕哺期及肝肾异常一律由医师/药师核对。

Skills扩至六组，服务边界常驻六角色；明确不替代医生诊断、不开处方、不评论其他医院或医生的方案。它们是模型提示约束，不是临床安全验证。已有知识中的“完全不提供症状方向/药品信息/报告整理”旧文字已按新范围修正；原医院/患者/预约事实不改变。

## 验证记录

- 2026-09-16：`.venv/Scripts/python.exe -m pytest tests/test_health_information.py tests/test_knowledge_skills.py -q` 最终 **97 passed（1.45s）**，其中64项健康信息、33项原知识/Skill回归。未运行真实模型、存储或上传。
- 健康信息检查覆盖成人/儿童/缺年龄/多患者年龄冲突、眼睛发红发痒、只返回真实三科ID、未知症状、否定与科普、明显急症/39.6℃/40度、中小月龄发热和低体温反例；不提供诊断结论。直接分诊复用了此次更新后的共享急症检测。
- 药品检查覆盖明确规格、标签数字/来源、未知药与缓释/复方/其他规格不作子串误匹配、已收录组合警示、未知组合不判安全、结果独立不改变目录。函数没有人口学参数，返回公开标签加特殊人群咨询警示；不是个人适用性筛查器，个体剂量请求需由角色/工具上下文继续约束。
- 24份知识（原17份加7份医疗资料）通过真实加载器及可控Chroma替身初始化、来源、重复导入、补缺回归；6组Skill通过角色匹配和Triage/Medication实际`_build_system_prompt`读取、修改文件后未reload不变/reload后生效检查。不是只查文件名或只检查卡片存在。
- 初轮旧测试将正文中的“医院公开信息”误作Skill标题，修正为匹配`### 医院公开信息`后通过；没有为通过断言删去常驻业务边界。
- 旧SkillManager影响由root核对为MEDIUM/5直接；3个调整契约数量的既有测试符号补充GitNexus上游查询均为UNKNOWN/0，并显示索引落后1提交，源码搜索只找到pytest定义。新模块未自行建图。限定范围`git diff --check`无空白错误（仅LF/CRLF提示）。

上述结果证明有界数据和代码接线，不证明真实模型总会遵守、RAG召回质量、OCR准确性或临床有效性；这些不得作为医学安全认证。后续组件、浏览器及模型验收属于U001-12/13。
