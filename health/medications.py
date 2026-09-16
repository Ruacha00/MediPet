"""有限、明确剂型的公开标签查询；不提供个体处方或组合安全判定。"""

import json
import re
from pathlib import Path

from health.models import GuidanceSection, MedicationInfo, SourceCitation


_DATA = Path(__file__).with_name("data")


def _normalize(value: str) -> str:
    return re.sub(r"[\s（）()]", "", value).casefold().replace("毫克", "mg")


def medication_information(drug_name: str, *, other_drugs: list[str] | None = None) -> MedicationInfo:
    """精确匹配通用名/已收录规格；未知名称、剂型或组合正常返回未收录。"""
    requested = drug_name.strip() if isinstance(drug_name, str) else ""
    catalog = json.loads((_DATA / "medications.json").read_text(encoding="utf-8"))
    drug = next((item for item in catalog if _normalize(requested) in {_normalize(alias) for alias in item["aliases"]}), None)
    if drug is None:
        return MedicationInfo(
            title="药品资料未收录", drug_name=requested or "未提供药名", formulation="未核实",
            summary="未收录该药名、规格或剂型，不能推断其用途、用量或相互作用。",
            sections=[GuidanceSection(heading="需要补充", items=[
                "请提供药盒上的通用名、成分、剂型和规格，或向药师核对。当前目录仅含对乙酰氨基酚500 mg普通片与布洛芬200 mg普通包衣片的指定美国标签。",
                "不能把同名的缓释剂型、复方制剂或儿童液体按本目录替换；未知药物或组合不等于安全。",
            ])], sources=[],
        )
    sections = [GuidanceSection(**section) for section in drug["sections"]]
    if other_drugs:
        findings = []
        for other in dict.fromkeys(value.strip() for value in other_drugs if isinstance(value, str) and value.strip()):
            normalized = _normalize(other)
            # 仅同一已核实成分的已收录规格别名可归一，复方/缓释等未知制剂不猜成分。
            known = next((item for item in catalog if normalized in {_normalize(alias) for alias in item["aliases"]}), None)
            if known:
                normalized = _normalize(known["drug_name"])
            interaction = next((item for item in drug["interactions"] if normalized in {_normalize(alias) for alias in item["aliases"]}), None)
            findings.append(f"{other}：{interaction['message']}" if interaction else
                            f"{other}：本目录未收录与{drug['drug_name']}的该组合，不能判断能否同服；请咨询医师或药师。")
        sections.append(GuidanceSection(
            heading="本次合并用药核对",
            items=findings or ["未提供可核对的药名，无法判断组合。请补充准确通用名和规格。"],
        ))
    sources = json.loads((_DATA / "sources.json").read_text(encoding="utf-8"))
    return MedicationInfo(
        title=f"{drug['drug_name']}指定标签资料", drug_name=drug["drug_name"], formulation=drug["formulation"],
        summary="以下是有来源的美国具体产品标签摘录，不是给您的处方或用药建议；不能替代手中产品说明书或医师、药师判断。",
        sections=sections, sources=[SourceCitation(**sources[drug["source_id"]])],
    )
