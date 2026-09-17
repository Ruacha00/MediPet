import asyncio, json
from pathlib import Path
from evaluation import semantic_intent as e
from core.intent_embeddings import EmbeddingConfig
from core.intent_recognizer import IntentRecognizer
from core.emergency import detect_emergency

BASE=Path("docs/internal/updates/U002-semantic-intent/evidence/calibration")
async def main():
    ds=e.load_dataset(Path("evaluation/cases/semantic_intent/dev.json"), manifest_path=Path("evaluation/cases/semantic_intent/manifest.json"))
    plan=e.read_json(BASE/"search-plan.json")
    r=IntentRecognizer("no-network",calibration={})
    cal={"schema_version":1,"template_fingerprint":r.template_fingerprint,"pattern":{"rule_fingerprint":r.pattern_fingerprint,"min_score":plan["pattern_min_score"]},"backends":{}}
    await r.aclose()
    ranks={}
    for backend in ("semantic","hash"):
        ranks[backend]=await e.rank_dataset(ds,config=EmbeddingConfig(backend=backend),warm_queries=100)
        e.write_json(BASE/f"dev-{backend}-rank.json",ranks[backend])
        assert ranks[backend]["quality_valid"]
        assert ranks[backend]["provider"]["active_backend"] == backend
        assert ranks[backend]["failures"]["classification_count"] == 0
        assert ranks[backend]["failures"]["fallback_sample_count"] == 0
        assert ranks[backend]["performance"]["warm_failures"] == 0
        cal["backends"][backend]={"space_id":ranks[backend]["provider"]["space_id"],"min_score":1.0,"min_margin":2.0}
    fake=e.build_failure_fixture(ds,ranks["semantic"])
    e.write_json(BASE/"dev-injected-failure.json",fake)
    selections={}
    for backend in ("semantic","hash"):
        grid=await e.replay_grid(ds,rankings=ranks[backend],llm=fake,calibration=cal,backend=backend,combinations=plan[f"{backend}_grid"])
        e.write_json(BASE/f"dev-{backend}-grid.json",grid)
        eligible=[x for x in grid["results"] if (x["report"]["llm_failure"]["accuracy"] or 0)>=.9]
        def key(x):
            m=x["report"]["llm_failure"]; t=x["thresholds"]
            return (m["coverage"],m["accuracy"] or 0,t["min_score"],t["min_margin"]) if eligible else (m["accuracy"] or 0,m["coverage"],t["min_score"],t["min_margin"])
        best=max(eligible or grid["results"],key=key)
        cal["backends"][backend].update(best["thresholds"])
        selections[backend]={"index":best["index"],"thresholds":best["thresholds"],"dev_accuracy_qualified":bool(eligible),"llm_failure":best["report"]["llm_failure"],"primary_rank":ranks[backend]["metrics"]["primary"],"low_overlap_rank":ranks[backend]["metrics"]["low_literal_overlap"],"performance":ranks[backend]["performance"]}
    e.write_json(Path("config/intent_embedding_calibration.json"),cal)
    e.write_json(BASE/"dev-selection.json",{"search_plan_sha256":e.file_hash(BASE/"search-plan.json"),"calibration":cal,"calibration_sha256":e.fingerprint(cal),"selected":selections})
    print(json.dumps({b:{"thresholds":v["thresholds"],"accuracy_qualified":v["dev_accuracy_qualified"],"failure_accuracy":v["llm_failure"]["accuracy"],"coverage":v["llm_failure"]["coverage"],"rank_accuracy":v["primary_rank"]["accuracy"],"rank_macro_f1":v["primary_rank"]["macro_f1"]} for b,v in selections.items()}))
asyncio.run(main())
