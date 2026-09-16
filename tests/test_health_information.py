"""确定性健康信息契约；不调用模型或把规则测试作为临床验证。"""

import json
import shutil
from pathlib import Path

import pytest

from core.skill_loader import SkillManager
from health.medications import medication_information
from health.models import MedicationInfo, TriageGuidance
from health.triage import triage_symptoms


ROOT = Path(__file__).parents[1]


def serialized(value):
    return value.model_dump_json()


@pytest.mark.parametrize(("message", "age", "department"), [
    ("我30岁，咳嗽2天，流鼻涕", None, "dep_internal"),
    ("孩子7岁咳嗽3天", None, "dep_pediatrics"),
    ("咳嗽2天", "child", "dep_pediatrics"),
    ("腹胀2天", "adult", "dep_internal"),
    ("眼睛痒和眼红2天", None, "dep_ophthalmology"),
    ("眼睛发红发痒2天", None, "dep_ophthalmology"),
    ("眼睛发痒", None, "dep_ophthalmology"),
])
def test_supported_symptoms_reference_real_departments(message, age, department):
    result = triage_symptoms(message, age_group=age)
    assert isinstance(result, TriageGuidance)
    assert department in {item.department_id for item in result.recommended_departments}
    actual = {item["department_id"]: item["name"] for item in json.loads((ROOT / "hospital/demo_data.json").read_text(encoding="utf-8"))["departments"]}
    assert all(actual[item.department_id] == item.name for item in result.recommended_departments)
    assert result.sources and all(source.url.startswith("https://www.nhs.uk/") for source in result.sources)
    assert "不是诊断" in result.summary


@pytest.mark.parametrize("message", ["", "不舒服", "请帮我看皮肤上的斑", "拔牙挂哪个科"])
def test_unknown_symptoms_do_not_invent_department_or_diagnosis(message):
    result = triage_symptoms(message)
    assert result.recommended_departments == []
    assert result.missing_information and result.sources == []


@pytest.mark.parametrize("age", [None, "unknown", "宝宝和爸爸"])
def test_general_symptoms_require_age_before_department_mapping(age):
    result = triage_symptoms("咳嗽2天", age_group=age)
    assert not result.recommended_departments
    assert any("年龄" in item for item in result.missing_information)


def test_conflicting_ages_do_not_guess_whose_symptoms():
    result = triage_symptoms("我30岁，孩子6岁，咳嗽2天")
    assert not result.recommended_departments
    assert any("年龄" in item for item in result.missing_information)


@pytest.mark.parametrize("message", ["我咳嗽3个月了，该看哪个科？", "流鼻涕持续2个月，该挂哪个科？", "发热已有5个月"])
def test_symptom_duration_in_months_does_not_supply_child_age(message):
    result = triage_symptoms(message)
    assert result.recommended_departments == []
    assert any("年龄" in item for item in result.missing_information)
    assert result.title != "婴儿发热需及时就医"


@pytest.mark.parametrize("message", ["我家孩子3个月大，咳嗽2天", "宝宝3个月，咳嗽", "月龄是3个月，流鼻涕", "3个月大的孩子咳嗽"])
def test_explicit_month_age_still_supports_pediatric_direction(message):
    result = triage_symptoms(message)
    assert [item.department_id for item in result.recommended_departments] == ["dep_pediatrics"]


def test_child_symptom_duration_is_not_used_as_infant_fever_age():
    result = triage_symptoms("孩子8岁，发热断断续续3个月，没测体温")
    assert result.title != "婴儿发热需及时就医"
    assert [item.department_id for item in result.recommended_departments] == ["dep_pediatrics"]


def test_multiple_symptom_groups_remain_separate_without_disease_conclusion():
    result = triage_symptoms("成年人眼痒，腹胀2天")
    assert {item.department_id for item in result.recommended_departments} == {"dep_ophthalmology", "dep_internal"}
    assert {source.source_id for source in result.sources} == {"nhs-conjunctivitis", "nhs-stomach-ache"}


@pytest.mark.parametrize("message", ["没有咳嗽，没有眼痛", "不咳嗽也没有眼红", "眼痛是什么意思", "如果眼红该怎么办"])
def test_negated_or_general_information_is_not_current_symptom_evidence(message):
    result = triage_symptoms(message, age_group="adult")
    assert not result.recommended_departments
    assert result.title == "需要补充症状信息"


def test_negative_clause_does_not_hide_positive_clause():
    result = triage_symptoms("没有腹痛但是眼痒2天", age_group="adult")
    assert [item.department_id for item in result.recommended_departments] == ["dep_ophthalmology"]


@pytest.mark.parametrize("message", ["现在呼吸困难", "突然失去意识", "现在剧烈胸痛", "胸痛", "体温39.6℃", "发烧40度"])
def test_shared_emergency_preempts_normal_department_recommendation(message):
    result = triage_symptoms(message, age_group="adult")
    assert not result.recommended_departments
    assert "120" in result.summary and result.sources


@pytest.mark.parametrize("message", ["眼睛痛，看不清", "剧烈腹痛", "出现呕血"])
def test_urgent_eye_or_abdominal_information_does_not_offer_ordinary_booking(message):
    result = triage_symptoms(message, age_group="adult")
    assert not result.recommended_departments
    assert result.title == "需要尽快线下评估"


@pytest.mark.parametrize("message", ["宝宝2个月，发烧38℃", "宝宝5个月，体温39℃", "宝宝5个月发烧，没测体温"])
def test_young_infant_fever_does_not_wait_for_39_point_5_demo_threshold(message):
    result = triage_symptoms(message)
    assert not result.recommended_departments
    assert result.title == "婴儿发热需及时就医"
    assert result.sources[0].source_id == "nhs-child-fever"


@pytest.mark.parametrize("message", ["宝宝2个月，体温36.5℃，咳嗽", "宝宝5个月，体温38℃，咳嗽", "如果2个月婴儿发烧38℃是什么意思"])
def test_infant_reference_or_lower_temperature_does_not_invent_high_fever(message):
    result = triage_symptoms(message)
    assert result.title != "婴儿发热需及时就医"


@pytest.mark.parametrize(("name", "expected", "dose", "interval"), [
    ("对乙酰氨基酚", "对乙酰氨基酚", "500 mg", "每6小时2片"),
    ("paracetamol", "对乙酰氨基酚", "500 mg", "每6小时2片"),
    ("对乙酰氨基酚片（500毫克）", "对乙酰氨基酚", "500 mg", "每6小时2片"),
    ("布洛芬", "布洛芬", "200 mg", "每4至6小时1片"),
    ("布洛芬200mg普通片", "布洛芬", "200 mg", "每4至6小时1片"),
    ("IBUPROFEN", "布洛芬", "200 mg", "每4至6小时1片"),
])
def test_exact_catalog_label_and_source_are_returned(name, expected, dose, interval):
    result = medication_information(name)
    assert isinstance(result, MedicationInfo) and result.drug_name == expected
    assert dose in result.formulation and "美国标签" in result.formulation
    assert interval in serialized(result) and "24小时不得超过6片" in serialized(result)
    assert len(result.sources) == 1 and "dailymed.nlm.nih.gov" in result.sources[0].url
    assert "不是给您的处方" in result.summary
    assert all(word in serialized(result) for word in ("儿童", "孕哺", "肝肾", "医师", "药师"))


@pytest.mark.parametrize("name", ["", "阿莫西林", "布洛芬缓释胶囊", "布洛芬混悬液", "复方对乙酰氨基酚", "布洛芬400mg片", "泰诺", "8岁孩子布洛芬吃多少", "acetaminophen extended release"])
def test_unknown_drug_formulation_or_question_is_not_substring_matched(name):
    result = medication_information(name)
    assert result.title == "药品资料未收录"
    assert result.sources == []
    assert "每6小时" not in serialized(result) and "每4至6小时" not in serialized(result)
    assert "不能推断" in result.summary


@pytest.mark.parametrize(("drug", "other", "warning"), [
    ("对乙酰氨基酚", "华法林", "先询问"),
    ("对乙酰氨基酚", "对乙酰氨基酚片500mg", "重复使用"),
    ("布洛芬", "阿司匹林", "心脑血管"),
    ("布洛芬", "萘普生", "胃肠出血"),
    ("布洛芬", "华法林", "胃肠出血"),
])
def test_only_label_supported_interactions_are_reported(drug, other, warning):
    result = medication_information(drug, other_drugs=[other, other])
    section = next(item for item in result.sections if item.heading == "本次合并用药核对")
    assert len(section.items) == 1 and warning in section.items[0]


@pytest.mark.parametrize("other", ["阿莫西林", "未知保健品", "对乙酰氨基酚", "布洛芬缓释胶囊"])
def test_unrecorded_combination_never_claims_no_interaction_or_safe(other):
    result = medication_information("布洛芬", other_drugs=[other])
    section = next(item for item in result.sections if item.heading == "本次合并用药核对")
    assert "未收录" in section.items[0] and "不能判断能否同服" in section.items[0]
    assert "没有相互作用" not in section.items[0] and "可以同服" not in section.items[0]


def test_results_are_independent_and_do_not_mutate_catalog():
    first = medication_information("布洛芬")
    first.sections[0].items.clear()
    first.sources[0].title = "changed"
    assert medication_information("布洛芬").sections[0].items
    assert medication_information("布洛芬").sources[0].title != "changed"


def test_six_role_skills_inject_medical_boundaries_without_extra_report_role():
    manager = SkillManager(str(ROOT / "skills"))
    loaded = manager.load()
    assert not manager.errors and len(loaded) == 6
    for role in ("general", "guidance", "appointment", "escalation", "triage", "medication"):
        prompt = manager.prompt_for("嗯", role)
        assert "不替代医生诊断" in prompt and "不开处方" in prompt
        assert "不评论其他医院或医生的诊疗方案" in prompt
        assert "39.5℃" in prompt and "120" in prompt
    assert "症状分诊与报告整理" in manager.prompt_for("报告", "triage")
    assert "有限药品标签信息" in manager.prompt_for("这个药", "medication")
    assert all("report" not in item.agents for item in loaded)


def test_new_knowledge_sources_are_official_and_contents_cover_each_requirement():
    documents = list((ROOT / "knowledge").glob("health-*.md"))
    assert len(documents) == 7
    texts = [path.read_text(encoding="utf-8") for path in documents]
    for text in texts:
        header = text.split("---", 2)[1]
        assert all(key in header for key in ("doc_id:", "title:", "source_id:", "source: https://"))
    all_text = "\n".join(texts)
    assert all(word in all_text for word in ("普通感冒", "结膜炎", "参考区间", "华法林", "NSAID", "39.5℃"))


def test_new_role_system_prompts_consume_skills_and_reload(tmp_path):
    from agents.agent_orchestrator import MedicationAgent, Request, TriageAgent

    shutil.copytree(ROOT / "skills", tmp_path / "skills")
    manager = SkillManager(str(tmp_path / "skills"))
    manager.load()
    request = Request(message="请说明这个问题", user_id="demo_user", conv_id="skill-only")
    triage = TriageAgent(client=None, model="fake", skill_manager=manager)
    medication = MedicationAgent(client=None, model="fake", skill_manager=manager)
    assert "症状分诊与报告整理" in triage._build_system_prompt(request)
    assert "有限药品标签信息" in medication._build_system_prompt(request)
    for agent in (triage, medication):
        assert "不评论其他医院或医生的诊疗方案" in agent._build_system_prompt(request)
    path = tmp_path / "skills/health_triage/SKILL.md"
    path.write_text(path.read_text(encoding="utf-8") + "\n核验标记：先核对原报告。", encoding="utf-8")
    assert "核验标记" not in triage._build_system_prompt(request)
    manager.reload()
    assert "核验标记" in triage._build_system_prompt(request)
    assert "核验标记" not in medication._build_system_prompt(request)
