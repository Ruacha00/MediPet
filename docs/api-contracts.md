# MediPet 业务数据与接口契约

本文约定医院业务数据、身份作用域、HTTP 响应和业务卡片结构，与 [`hospital/models.py`](../hospital/models.py) 保持一致。前后端契约测试共同读取文末 JSON 样例。

## 类型与序列化

- 使用现有的 dataclass 请求/编排结果及 Pydantic API 模型。新增具体领域模型集中于 `hospital/models.py`，无新业务框架或通用仓储。
- `ContractModel` 拒绝未定义字段。构造用 `Model.model_validate(data)`，Redis 保存用 `model_dump_json()`、恢复用 `model_validate_json(raw)`；工具/HTTP 输出用 `model_dump(mode="json")`。禁止将模型、datetime 或枚举原对象直接传给工具的 `json.dumps(result)`。
- ID 是区分大小写的非空 ASCII 字符串，匹配 `[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}`；用户正文及姓名不充当 ID。静态数据 ID 由预置数据固定；运行创建的事项、方案、预约、消息、列表用类型前缀加完整 UUID hex，例如 `visit-<uuid32>`。现有短 `request_id` 仅用于追踪，不作为业务主键。
- 日期固定为 `YYYY-MM-DD`；排班时刻为本地 `HH:MM`；星期 1=周一、7=周日；时段为 `morning/afternoon`。今天、明天、后天均按 `Asia/Shanghai` 求日期。
- `created_at/updated_at/queried_at/expires_at/executed_at/cancelled_at` 为带时区 ISO 8601 时间；服务写入上海时区 `+08:00`，模型也能读取合法 UTC/其他偏移并比较同一时刻。拒绝无时区、数值纪元时间和无效日期；测试注入固定时钟。
- 费用统一 `fee_fen: int >= 0`，容量和剩余数也是严格整数，拒绝布尔值、浮点值和数字字符串。展示时转换为元，不用浮点字段保存费用。
- 验证只确认输入形状及记录内部一致性。身份权限、目标存在、当前库存、当前方案引用和状态转换仍必须由业务服务从存储检查，不能以“模型校验通过”代替事务校验。

## 身份、事项与现有消费者

默认演示参与者固定为 `DEFAULT_USER_ID = "anonymous"`，保留现有 `ChatRequest` 默认。预置数据为该参与者提供本人、家属；前端使用同一默认值，不保留 `u1001` 与后端不一致的默认。未知参与关系返回业务错误，不自动把其他 ID 映射到 anonymous；这不是登录系统。

`Patient` 保存 `patient_id/user_id/name/relationship(self|family)`，足够表达本次一名参与者的两名就诊人，不建设患者共享或身份核验平台。默认患者为该参与者唯一的 `self` 患者。`Visit` 保存 `user_id/patient_id/conv_id/title/archived/created_at/updated_at`；`conv_id` 就是事项 ID，一个事项创建后患者不可变。

| 消费者 | 接口约定 |
| --- | --- |
| `api/main.py::ChatRequest` | 保留 `message/user_id/conv_id`；新增 `patient_id: Optional[str] = None`。无事项时根据所选或默认患者创建；已有事项加载绑定患者，显式冲突返回 `identity_conflict`；显式未知 conv_id 不隐式新建。 |
| `agents/...::Request` | 末尾新增默认 None 的 `patient_id`，保留旧直接调用形状；业务工具仅处理服务器已校验的非空身份。测试/评测的业务路径必须提供受控身份上下文。 |
| `VisitIdentity` | 从服务器加载事项后构造必填的 `user_id/patient_id/conv_id`，传入服务；模型 args 不含这些字段。医院服务也核对目标记录归属。 |
| `AgentResponse/OrchestratorResult` | 新增 `artifacts: list[Artifact]`，dataclass 使用 `field(default_factory=list)`；保留原路由与 trace 字段。 |
| `ChatResponse` | 保留原字段和 `conv_id`，增加 `patient_id: str`、`visit: Visit`、`artifacts: list[Artifact]`。患者详情从 `/patients` 获取；visit 必须与响应 IDs 一致。 |
| `entities` | 继续 `dict[str,list[str]]`。键为 `department/doctor/date/period/slot_id/appointment_id/origin/destination/accessibility/selection_index`；日期值用 ISO 日期、时段用枚举、选择位置用 `"1"` 等一基数字字符串，无障碍用 `"normal"/"accessible"`。科室/医生可以是待解析名称，服务解析至固定 ID；身份不从实体提取。 |

归档只影响默认列表，保留历史和预约。归档事项可读取但不得发送新消息或确认新操作；先恢复事项。事项改名、归档、恢复的 `updated_at` 不早于 `created_at`。

## 静态事实与号源

`DemoData` 对应 `hospital/demo_data.json`：`hospital,departments,doctors,locations,schedules,checklists,wayfinding,contact_info,patients`。具体类型为 `HospitalInfo/Department/Doctor/Location/ScheduleTemplate/VisitChecklist/Wayfinding/ContactInfo/Patient`。模型检查静态 ID 重复、目录引用、排班医生与科室关联、指引地点；范围及内容一致性由预置数据测试校验。

`Source{source_id,title}` 是清单、目录、指引和联系信息可展示的来源；知识文档另沿用检索结果中的标题/来源字段。`ContactInfo.delivery` 固定 `contact_only`，`summary` 是可复制文字，不代表已提交人工。

`ScheduleTemplate` 固定医生/科室、星期、时段、起止、容量、费用。号源服务用固定日期、排班 ID 和时段构建稳定号源 ID，同一天重复初始化不得产生新 ID。`SlotDetails` 保存展示事实，`Slot` 再增加 `capacity/remaining`；`0 <= remaining <= capacity`。未来七天窗口含当天至第六天，使用上海日期；当天已结束时段不可选择，具体筛选由固定时钟测试验证。

`SlotQuery` 的科室、医生、日期、时段均可缺省，缺省表示不按该条件筛选。`SlotList{list_id,query,slots,queried_at}` 反映一次真实查询；无号源返回 `ServiceResult(success=false,error_code="no_slots",...)`，可附空 `slot_list` 用于展示。该列表只作查询快照，选择及确认都需重读库存。

## 方案、预约与确认

`AppointmentSnapshot{patient_name,slot:SlotDetails}` 是服务生成的确认资料，不保存动态 `remaining`。`AppointmentProposal` 的字段：身份三元组、`proposal_id`、`operation(create|cancel)`、`target_id`、`snapshot`、`status`、`created_at/expires_at`、可空 `result:ExecutionReceipt`。

- 创建方案的 `target_id` 为 `slot_id`，必须与快照一致；取消方案为实际 `appointment_id`，快照复制该预约资料。两者仅准备资料，不变更预约或库存。
- `status` 只取 `pending/executed/expired/superseded`；默认期限 `DEFAULT_PROPOSAL_TTL_SECONDS=900`，运行配置可调整。`now >= expires_at` 即过期，回放已执行方案不重新判断当前时间。
- `AppointmentRecord` 包含身份、`appointment_id/snapshot/status/created_at/cancelled_at`。其 `conv_id` 永远是创建预约的原事项；按患者查询可以在同患者的新事项发起取消。`active` 没有取消时间，`cancelled` 必须有不早于创建的取消时间，取消不改写原事项归属。
- `ExecutionReceipt` 包含本次操作的 `user_id/patient_id/conv_id` 及 `receipt_id/proposal_id/operation/status="executed"/appointment/executed_at`。`appointment` 是本次执行时的完整记录快照；后续取消不改写过去创建回执。创建时回执与预约的事项相同；取消只要求预约同参与者、同患者，允许预约来自其他事项。方案和 ConfirmResponse 与本次回执的身份三元组相同。`receipt_id` 使用 `receipt:<proposal_id>`，同方案只生成一个；失败不是执行回执。
- 只有 executed 方案保存 result，且结果的方案、操作、身份、目标、快照及执行时间必须匹配。服务在事务内保存原回执；重复确认返回原记录和同一个 `receipt_id`。

确认顺序：加载当前事项身份 → 校验方案归属 → 已 executed 则返回已保存结果 → 对 pending 方案校验当前方案引用、期限、目标状态/号源 → Redis 事务同时更新方案、预约、号源和当前引用。执行或失效后清除当前引用；清除只发生在该引用仍等于本方案时。并发冲突返回可重试 `conflict`，不得返回伪成功。

聊天中的“确认预约”只重新展示待确认资料。执行唯一入口是页面确认请求；工具白名单不提供无条件创建/取消函数。

## 最近选择、方案替换与历史

`SelectionState` 包含身份、`status(ready|empty|failed)`、`list_id/query/slots/queried_at/current_proposal_id`，按事项持久保存。

- 成功且非空的号源查询替换完整列表并置 ready；成功空结果置 empty，清空 list_id/slots；查询失败置 failed，同样清除旧可选列表。保留本次 query 以支持后续“明天呢”，但不能据失效列表选“第一个”。
- `selection_index` 是一基位置，只能在当前 ready 列表上解释；直接 slot_id 也必须验证真实数据。排序固定为日期、时段起点、医生 ID、号源 ID。
- 列表更新本身不执行预约，也不自动取消当前方案。选择变化、准备创建或取消的新方案时，将旧 pending 方案标为 superseded，再设置新引用；同事项最多一个当前待确认方案。
- `HospitalStore` 与 `VisitStore` 复用同一 Redis 连接/数据库。选择及当前方案键集中由 VisitStore 构建；它必须给医院服务提供可纳入同一事务的最小键/操作访问，不能只提供内部自行提交的更新函数。医院服务在一个事务里比较当前引用、淘汰旧方案、保存新方案，或确认执行。选择状态与预约服务共享事务所需的键。

`VisitMessage` 是完整历史记录，字段为身份、`message_id/role/content/created_at/kind/artifacts/metadata/proposal_id/receipt_id`。`role` 沿用 user/assistant/system；`kind` 为 chat、confirmation_event、operation_result。确认事件必须带 proposal_id，成功业务结果还带 receipt_id。确认成功的两条消息使用 `confirm:<receipt_id>`、`result:<receipt_id>` 作为稳定消息 ID，并以一次追加操作去重；重复请求不得新增第二条预约成功事实。

完整历史的消息/卡片不设工作记忆 TTL，不因归档、压缩而删除。恢复工作窗口时，将 `created_at` 转为既有 `Message.timestamp`，其余普通 role/content/metadata 直接使用；工作记忆 JSON 的旧 `ts` 是内部字段，不对外复用。历史接口按最早到最新返回，`message_id` 供前端去重。恢复不能重新执行历史工具或确认事件。

## Artifact 与本轮结果

固定包装 `Artifact{id,type,data}`，十类data对应下表。模型按type检查data并生成可JSON序列化的字典；RAG的`results/reranked`保留独立检索接口。

| type | data 模型 |
| --- | --- |
| catalog | `CatalogData{category,items}`，items 类型必须匹配 hospital/department/doctor/location |
| slot_list | `SlotList` |
| appointment_proposal | `AppointmentProposal` |
| appointment_record | `AppointmentRecord` |
| visit_checklist | `VisitChecklist` |
| wayfinding | `Wayfinding` |
| contact_info | `ContactInfo` |
| triage_guidance | `TriageGuidance` |
| medication_info | `MedicationInfo` |
| report_summary | `ReportSummary` |

ID 代表具体业务快照，不直接使用可变预约 ID：方案为 `proposal:<proposal_id>:<status>`；确认产生的预约卡为 `appointment:<appointment_id>:<receipt_id>`；列表为 `slots:<list_id>`；独立查询/静态卡可用 `type:<uuid32>`。相同业务快照跨透传环节保持 ID，相同 ID/data 去重；同 ID 不同 data 属于契约错误，响应整合器丢弃该冲突 ID 的所有版本、保留其他卡片，并记录 `artifact_conflict` trace，不让模型选版本。服务生成的资源 ID 按上述短类别前缀加 UUID hex 格式，组合卡片 ID 可保持在 128 字符内。

透传路径为工具结果 → 本轮AgentResponse → OrchestratorResult → ChatResponse → VisitMessage；整合器只改文字。工具轨迹与卡片保存在请求局部结果中，避免同实例并发污染，保留三次模型请求上限。

## 接口与错误

以下为患者事项与预约接口；其他接口以服务生成的OpenAPI文档为准。以下列表输出均按 `items` 包装，字段值用本文件模型的 JSON 形式。

| 接口 | 输入 | 成功输出 |
| --- | --- | --- |
| `GET /patients` | query user_id，缺省 anonymous | `{items:Patient[]}` |
| `GET /visits` | query user_id、patient_id 必填、archived 缺省 false | `{items:Visit[]}` |
| `POST /visits` | `{user_id?,patient_id,title?}`；缺省标题“新的就诊事项” | `Visit`，HTTP 201 |
| `PATCH /visits/{conv_id}` | `{user_id?,title?,archived?}`，至少一个变更字段 | 更新后的 `Visit`；不接受 patient_id |
| `GET /visits/{conv_id}/messages` | query user_id | `{visit:Visit,items:VisitMessage[]}`，含完整有序历史 |
| `POST /chat` | 扩展后的 ChatRequest | 扩展后的 ChatResponse，新增字段见前表；原主辅角色、意图等保留 |
| `POST /appointment-proposals/{proposal_id}/confirm` | `ConfirmRequest{user_id?,conv_id}` | `ConfirmResponse{user_id,patient_id,conv_id,receipt,artifacts}`，HTTP 200；不含 Agent/意图/模型 trace |

医院工具统一返回 `ServiceResult` 字典。HTTP 将业务失败映射为 `{"detail":{"code":"...","message":"...","retryable":false}}`（`BusinessError`）；保留字符串错误说明供现有 trace 使用。只在这些新增/适配业务路径映射，不建设全局异常框架。FastAPI/Pydantic 的请求形状错误继续用原生 422 `detail` 数组；前端需同时支持它和业务 detail 对象。

| error_code | 含义 | HTTP 状态 | retryable |
| --- | --- | --- | --- |
| not_found | 患者、事项、号源、预约、地点或路线不存在 | 404 | false |
| missing_fields / invalid_input | 业务所需输入不足或无法解释 | 422 | false |
| identity_conflict | 参与关系或患者/事项/目标不一致 | 403 | false |
| visit_archived | 归档事项尚未恢复 | 409 | false |
| selection_unavailable | 无有效列表或选择位置越界 | 409 | false |
| no_slots / target_unavailable | 无可用号源、目标状态不能执行 | 409 | false |
| proposal_expired | 方案到期 | 410 | false |
| proposal_superseded | 方案已被改选，或不再是当前方案 | 409 | false |
| conflict | Redis 并发比较/事务冲突 | 409 | true |
| storage_unavailable | 所需存储不可用 | 503 | true |
| artifact_conflict | 同轮相同卡片 ID 内容冲突 | 500（仅直接业务失败时） | false |

工具查询失败通过 ServiceResult 留在聊天/trace 中，普通聊天仍返回自身成功完成的响应；不是每个工具空结果都把 `/chat` 变成非 2xx。业务失败不得生成成功预约卡。一个独立工具结果只含该工具的数据；已有其他成功工具卡片由响应整合器保留。

## 十类卡片完整样例

以下为契约测试夹具，不是已生成预约或最终医院数据。演示内容统一由预置数据提供；ID 和名称可以替换，字段不可各自更名。测试会读取此 JSON 并验证十类 Artifact 以及嵌套模型。

<!-- artifact-examples -->
```json
[
  {
    "id": "catalog-example",
    "type": "catalog",
    "data": {
      "category": "hospital",
      "items": [
        {
          "hospital_id": "minghe",
          "name": "明和虚构医院",
          "description": "单医院门诊演示数据",
          "address": "演示市明和路1号（虚构）",
          "outpatient_hours": "08:00—17:00",
          "source": {
            "source_id": "hospital-public",
            "title": "明和虚构医院公开说明"
          }
        }
      ]
    }
  },
  {
    "id": "slots:list-example",
    "type": "slot_list",
    "data": {
      "list_id": "list-example",
      "query": {
        "department_id": "dep_pediatrics",
        "doctor_id": null,
        "date": "2026-09-17",
        "period": "morning"
      },
      "slots": [
        {
          "slot_id": "slot-example",
          "hospital_name": "明和虚构医院",
          "department_id": "dep_pediatrics",
          "department_name": "儿科",
          "doctor_id": "doctor_xu",
          "doctor_name": "许知宁",
          "date": "2026-09-17",
          "period": "morning",
          "start_time": "09:00",
          "end_time": "11:00",
          "fee_fen": 2000,
          "location_id": "pediatrics-room",
          "location_name": "门诊楼二层儿科",
          "capacity": 5,
          "remaining": 3
        }
      ],
      "queried_at": "2026-09-16T10:00:00+08:00"
    }
  },
  {
    "id": "proposal:proposal-example:pending",
    "type": "appointment_proposal",
    "data": {
      "user_id": "anonymous",
      "patient_id": "patient_child",
      "conv_id": "visit-example",
      "proposal_id": "proposal-example",
      "operation": "create",
      "target_id": "slot-example",
      "snapshot": {
        "patient_name": "林小满",
        "slot": {
          "slot_id": "slot-example",
          "hospital_name": "明和虚构医院",
          "department_id": "dep_pediatrics",
          "department_name": "儿科",
          "doctor_id": "doctor_xu",
          "doctor_name": "许知宁",
          "date": "2026-09-17",
          "period": "morning",
          "start_time": "09:00",
          "end_time": "11:00",
          "fee_fen": 2000,
          "location_id": "pediatrics-room",
          "location_name": "门诊楼二层儿科"
        }
      },
      "status": "pending",
      "created_at": "2026-09-16T10:01:00+08:00",
      "expires_at": "2026-09-16T10:16:00+08:00",
      "result": null
    }
  },
  {
    "id": "appointment:appointment-example:receipt:proposal-example",
    "type": "appointment_record",
    "data": {
      "user_id": "anonymous",
      "patient_id": "patient_child",
      "conv_id": "visit-example",
      "appointment_id": "appointment-example",
      "snapshot": {
        "patient_name": "林小满",
        "slot": {
          "slot_id": "slot-example",
          "hospital_name": "明和虚构医院",
          "department_id": "dep_pediatrics",
          "department_name": "儿科",
          "doctor_id": "doctor_xu",
          "doctor_name": "许知宁",
          "date": "2026-09-17",
          "period": "morning",
          "start_time": "09:00",
          "end_time": "11:00",
          "fee_fen": 2000,
          "location_id": "pediatrics-room",
          "location_name": "门诊楼二层儿科"
        }
      },
      "status": "active",
      "created_at": "2026-09-16T10:02:00+08:00",
      "cancelled_at": null
    }
  },
  {
    "id": "visit_checklist:example",
    "type": "visit_checklist",
    "data": {
      "checklist_id": "child-first",
      "department_id": "dep_pediatrics",
      "visit_type": "child",
      "title": "儿童就诊材料",
      "items": [
        "就诊人身份证明",
        "既往就诊资料"
      ],
      "source": {
        "source_id": "child-materials",
        "title": "儿童就诊准备说明"
      }
    }
  },
  {
    "id": "wayfinding:example",
    "type": "wayfinding",
    "data": {
      "route_id": "hall-pharmacy-accessible",
      "origin_id": "hall",
      "destination_id": "pharmacy",
      "mode": "accessible",
      "steps": [
        "从门诊大厅沿无障碍标识前行。",
        "在一层药房窗口办理。"
      ],
      "source": {
        "source_id": "hospital-wayfinding",
        "title": "明和虚构医院文字指引"
      }
    }
  },
  {
    "id": "contact_info:example",
    "type": "contact_info",
    "data": {
      "contact_id": "guide-desk",
      "label": "人工导诊联系信息",
      "phone": "010-00000000（演示号码）",
      "hours": "08:00—17:00",
      "location": "门诊大厅导诊台",
      "source": {
        "source_id": "hospital-contact",
        "title": "明和虚构医院导诊说明"
      },
      "summary": "我希望了解儿童门诊报到流程。",
      "delivery": "contact_only"
    }
  },
  {
    "id": "triage_guidance:example",
    "type": "triage_guidance",
    "data": {
      "title": "需要补充症状信息",
      "summary": "请补充年龄和症状持续时间；此信息不替代医生诊断。",
      "recommended_departments": [],
      "missing_information": [
        "就诊人年龄"
      ],
      "sources": []
    }
  },
  {
    "id": "medication_info:example",
    "type": "medication_info",
    "data": {
      "title": "药品资料未收录",
      "summary": "请核对药名和剂型后咨询药师。",
      "drug_name": "未提供药名",
      "formulation": "未核实",
      "sections": [],
      "sources": []
    }
  },
  {
    "id": "report_summary:example",
    "type": "report_summary",
    "data": {
      "title": "报告文字整理",
      "summary": "缺少明确参考区间，保留原文供核对。",
      "input_kind": "text",
      "extracted_text": "WBC 6.2",
      "observations": [],
      "warnings": [
        "缺少单位和参考区间，不能判断是否超出区间。"
      ],
      "sources": []
    }
  }
]
```

同患者跨事项取消的完整回执样例：在 `visit-followup` 取消 `visit-example` 创建的预约；预约的原 conv_id 保留，确认事件写入 visit-followup。

<!-- cancellation-example -->
```json
{
  "user_id":"anonymous","patient_id":"patient_child","conv_id":"visit-followup",
  "receipt_id":"receipt:proposal-cancel-example","proposal_id":"proposal-cancel-example",
  "operation":"cancel","status":"executed","executed_at":"2026-09-16T10:10:00+08:00",
  "appointment":{
    "user_id":"anonymous","patient_id":"patient_child","conv_id":"visit-example",
    "appointment_id":"appointment-example",
    "snapshot":{"patient_name":"林小满","slot":{"slot_id":"slot-example","hospital_name":"明和虚构医院","department_id":"dep_pediatrics","department_name":"儿科","doctor_id":"doctor_xu","doctor_name":"许知宁","date":"2026-09-17","period":"morning","start_time":"09:00","end_time":"11:00","fee_fen":2000,"location_id":"pediatrics-room","location_name":"门诊楼二层儿科"}},
    "status":"cancelled","created_at":"2026-09-16T10:02:00+08:00","cancelled_at":"2026-09-16T10:10:00+08:00"
  }
}
```
