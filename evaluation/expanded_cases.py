"""Frozen, authored workloads for résumé metrics. No model-generated labels."""
import copy
import json
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLOCK = "2026-09-16T10:00:00+08:00"
DEPARTMENTS = [("儿科", "dep_pediatrics", "patient_child"), ("内科", "dep_internal", "patient_self"), ("眼科", "dep_ophthalmology", "patient_self")]


def business_cases():
    cases = []
    create_queries = ["请列出{day}{dep}可预约的号源。", "想去{dep}就诊，看看{day}有哪些号可以选。", "帮我查{day}的{dep}门诊号。", "{day}准备去{dep}，还有哪些预约时段？", "我需要查询{dep}{day}的剩余号源。"]
    choose = ["选列表第{n}项，请生成待确认预约。", "把第{n}个号整理成预约方案。", "我选第{n}条，先展示预约确认资料。", "就用第{n}个可选号，帮我准备预约。", "请为列表中的第{n}个号生成确认单。"]
    cancel_queries = ["查看当前就诊人的有效预约。", "请列出我已经约好的门诊记录。", "帮我查询尚未取消的预约。", "展示这个患者的预约记录。", "我之前的挂号记录有哪些仍有效？"]
    cancel = ["请为第一条预约生成取消方案。", "列表第一项不去了，先展示取消确认信息。", "准备取消第一个预约。", "第一条需要退掉，请生成待确认取消方案。", "请把首条预约做成取消确认单。"]
    compounds = [
        "请查{day}{dep}的可选号源，同时列出{who}首次就诊材料。",
        "{day}去{dep}，想知道还有哪些号，以及{who}第一次到院要带哪些资料。",
        "帮我查{dep}{day}剩余的号，再给一份{who}初次就诊准备清单。",
        "我需要{day}{dep}的号源列表，也要了解{who}首次就诊需带的证件。",
        "能一起查{day}{dep}可选时段和{who}首次就诊材料吗？",
        "{who}{day}要看{dep}，请查号并说明第一次到院需要的资料。",
        "麻烦列出{day}{dep}能约的号，并展示{who}初诊材料清单。",
        "{day}{dep}还有号吗？另外，{who}首次来院需要准备哪些材料？",
        "查一下{dep}{day}的号源，顺便把{who}第一次来就诊的材料列出来。",
        "请把{day}{dep}的剩余号源和{who}首次就诊准备事项一起给我。",
        "{who}计划{day}去{dep}初诊，能选哪些号，要带什么资料？",
        "给我{day}{dep}门诊号列表，同时说明{who}首次就诊所需材料。",
        "我想了解{dep}{day}哪些时段有号，也想要{who}首次就诊的准备清单。",
        "{day}到{dep}，请同时查询号源与{who}第一次就诊的材料要求。",
        "先列{day}{dep}可预约号，再列{who}首次就诊证件资料。",
        "{who}{day}第一次去{dep}，请查可选门诊号，并列明要带的东西。",
        "请查{day}{dep}剩余号源；也请说明{who}初诊要携带的材料。",
        "{dep}{day}还能预约哪些号？{who}第一次就诊的准备清单也请给我。",
        "麻烦一并查询{day}{dep}号源和{who}首次就诊所需证件材料。",
        "需要{day}{dep}可选门诊时段，以及{who}初次就诊材料，两项都请帮忙查。",
    ]

    def make(family, i, turns, assertions, dep, dept_id, patient, day, **extra):
        case = {"id": f"expanded-{family}-{i+1:02d}", "clock": CLOCK, "patient_id": patient, "user_id": "anonymous",
                "turns": turns, "assertions": assertions, **extra}
        cases.append({"family": family, "expected": {"department_id": dept_id, "date": day, "patient_id": patient}, "case": case})

    for i in range(20):
        dep, dept_id, patient = DEPARTMENTS[i % 3]
        day = str(date(2026, 9, 16) + timedelta(days=1 + (i // 3) % 6))
        n = "一" if i % 2 == 0 else "二"
        query = create_queries[i % 5].format(day=day, dep=dep)
        make("create", i, [query, choose[i % 5].format(n=n)], ["before_confirmation.appointments.count=0", "after_confirmation.appointments.count=1", "after_confirmation.slot_delta=-1", f"query.department_id={dept_id}", f"query.date={day}"], dep, dept_id, patient, day, after_turns=[{"action": "confirm_current"}])
        cases[-1]["expected"]["selection_index"] = i % 2
        make("cancel", i, [cancel_queries[i % 5], cancel[i % 5]], ["before_confirmation.appointment.status=active", "after_confirmation.appointment.status=cancelled", "after_confirmation.slot_delta=1"], dep, dept_id, patient, day, setup=[{"action": "create_existing_appointment", "department": dep, "date": day}], after_turns=[{"action": "confirm_current"}])
        who = "儿童" if patient == "patient_child" else "成人"
        make("compound", i, [compounds[i].format(day=day, dep=dep, who=who)], ["has_artifacts=slot_list,visit_checklist", "appointments.count=0", f"query.department_id={dept_id}", f"query.date={day}"], dep, dept_id, patient, day)
        if i < 5:
            next_day = str(date.fromisoformat(day) + timedelta(days=1))
            # Keep target within seven-day supply; use a prior initial day when necessary.
            if next_day > "2026-09-22":
                next_day = "2026-09-22"
            turns = [query, f"日期改成{next_day}，科室不变。", "只看下午的。"]
            assertions = [f"query.department_id={dept_id}", f"query.date={next_day}", "query.period=afternoon"]
            make("context", i, turns, assertions, dep, dept_id, patient, next_day)
        elif i < 10:
            make("context", i, [query, choose[i % 5].format(n=n)], [f"proposal.target_id=last_slot_list.slots[{i%2}].slot_id", "appointments.count=0", f"query.department_id={dept_id}", f"query.date={day}"], dep, dept_id, patient, day)
        elif i < 15:
            make("context", i, [query, f"请给刚才列表第{n}个号准备预约方案。"], ["complete_history.count.unchanged=true", f"proposal.target_id=saved_slot_list.slots[{i%2}].slot_id", "appointments.count=0"], dep, dept_id, patient, day, between_turns={"1": [{"action": "expire_working_memory"}]})
        else:
            make("context", i, [query, "选择第一项，生成待确认预约方案。", "改选第二个号，重新出确认方案。"], ["old_proposal.status=superseded", "current_proposal.target_id=last_slot_list.slots[1].slot_id", "appointments.count=0"], dep, dept_id, patient, day)
    # The existing evaluator's old-proposal fact compares first and final snapshots;
    # reselection must first prepare a proposal in the first turn to activate it.
    for item in cases:
        if item["family"] == "context" and int(item["case"]["id"].rsplit("-", 1)[1]) >= 16:
            item["case"]["turns"] = [item["case"]["turns"][0] + "并把第一个号做成待确认方案。", "改选第二个号，重新出确认方案。"]
    assert len(cases) == 80 and len({c["case"]["id"] for c in cases}) == 80
    return cases


def retrieval_cases():
    # Required supporting documents, specified from content before any retrieval run.
    rows = [
        ("第一次来门诊，身份证明、预约凭证和旧病历应该怎么准备？", ["first-visit-materials"]),
        ("带孩子就诊，监护人和孩子分别要带什么证件？", ["child-visit-materials"]),
        ("已经约好门诊，出发之前一般要整理哪些资料？", ["general-preparation"]),
        ("我到医院之后应该先找哪里核对科室和预约时间？", ["arrival-checkin"]),
        ("在聊天里说确认算挂号成功吗，实际还要点哪个按钮？", ["appointment-howto"]),
        ("取消预约时需要先选记录吗，取消后号源会释放吗？", ["appointment-cancellation"]),
        ("儿科和内科的演示挂号费分别是多少？", ["appointment-fees"]),
        ("有人推轮椅，院内无障碍路线支持哪些地点和设施？", ["accessible-wayfinding"]),
        ("需要人工帮我核对材料，导诊台几点有人，在哪儿？", ["human-guidance"]),
        ("我想了解门诊的营业时间和地址。", ["hospital-overview"]),
        ("内科诊区在哪层，负责普通门诊的医生是谁？", ["department-internal"]),
        ("儿童看病的诊区在哪层，医生叫什么名字？", ["department-pediatrics"]),
        ("眼科的诊区楼层、医生和报到准备是什么？", ["department-ophthalmology"]),
        ("拿到院方有效取药凭证之后，去哪里核对信息领药？", ["medicine-pickup"]),
        ("去做检验的办理地点在哪里，能否从这里确定空腹要求？", ["lab-process"]),
        ("收费处在哪里，这个系统能替我支付费用吗？", ["payment-process"]),
        ("把就诊事项归档后，聊天和预约会一起被删除吗？", ["patients-and-visits"]),
        ("报告数值正常是否代表没有疾病，缺失的参考区间能补吗？", ["health-report-terms"]),
        ("普通病毒性感冒一定需要抗生素吗？", ["health-common-cold"]),
        ("小婴儿发热为什么需要结合年龄判断就医紧迫性？", ["health-child-fever"]),
        ("腹痛咨询前要记录哪些症状，哪些变化应及时求助？", ["health-abdominal-symptoms"]),
        ("红眼会不会有感染或过敏之外的原因，日常如何减少传播？", ["health-red-eye"]),
        ("布洛芬200毫克普通片资料为什么不能套到儿童混悬液？", ["health-ibuprofen-label"]),
        ("对乙酰氨基酚为什么不能和同成分药重复使用？", ["health-acetaminophen-label"]),
        ("预约方案多久失效，改选以后原来的方案还能点吗？", ["appointment-howto"]),
        ("同一个取消方案连续确认两次，会多释放一个号吗？", ["appointment-cancellation"]),
        ("页面显示20元，为什么底层预约费用记录为2000？", ["appointment-fees"]),
        ("我替家属看病，为什么需要切换到对方的事项？", ["patients-and-visits"]),
        ("没有收录的院内路线能不能自动规划出来？", ["accessible-wayfinding"]),
        ("提供导诊电话号码后，是否代表已经替我接通工作人员？", ["human-guidance"]),
        ("检查报告的单位不同，是否可以直接拿数字比较？", ["health-report-terms"]),
        ("已有布洛芬标签资料能否判断所有未知合用药都安全？", ["health-ibuprofen-label"]),
        ("为孩子准备证件后，到院应该怎样报到？", ["child-visit-materials", "arrival-checkin"]),
        ("想先了解预约如何确认，以及之后怎么取消。", ["appointment-howto", "appointment-cancellation"]),
        ("门诊几点开放，找人工导诊的联系方式是什么？", ["hospital-overview", "human-guidance"]),
        ("眼科的医生是谁，儿科的医生又是谁？", ["department-ophthalmology", "department-pediatrics"]),
        ("先讲儿童就诊需要的证件，再讲一般门诊准备资料。", ["child-visit-materials", "general-preparation"]),
        ("取药和缴费分别去什么位置办理？", ["medicine-pickup", "payment-process"]),
        ("解释怎么预约门诊，以及各科的预约费用。", ["appointment-howto", "appointment-fees"]),
        ("说一下检验办理流程，并解释报告参考区间的含义。", ["lab-process", "health-report-terms"]),
    ]
    return [{"id": f"retrieval-{i+1:02d}", "query": q, "required_doc_ids": docs} for i, (q, docs) in enumerate(rows)]


if __name__ == "__main__":
    import sys
    path = Path(sys.argv[1])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump({"business": business_cases(), "retrieval": retrieval_cases(), "clock": CLOCK}, stream, ensure_ascii=False, indent=2)
