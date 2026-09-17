import asyncio, json, os, importlib.util, sys
from pathlib import Path
from dotenv import load_dotenv
from evaluation import semantic_intent as e
from core.intent_embeddings import EmbeddingConfig
BASE=Path("docs/internal/updates/U002-semantic-intent/evidence/calibration")
MANIFEST=Path("evaluation/cases/semantic_intent/manifest.json")

async def main():
    load_dotenv(".env",override=True)
    assert os.environ.get("ANTHROPIC_API_KEY"), "Local model configuration missing"
    cal=e.read_json(Path("config/intent_embedding_calibration.json"))
    assert e.fingerprint(cal)==e.read_json(BASE/"dev-selection.json")["calibration_sha256"]
    reports={}
    for name,path in (("holdout",Path("evaluation/cases/semantic_intent/holdout.json")),("regression",Path("evaluation/cases/intents.json"))):
        ds=e.load_dataset(path,manifest_path=MANIFEST,allow_holdout=name=="holdout")
        ranks={}
        for backend in ("semantic","hash"):
            out=BASE/f"{name}-{backend}-rank.json"
            if out.exists(): ranks[backend]=e.read_json(out)
            else:
                ranks[backend]=await e.rank_dataset(ds,config=EmbeddingConfig(backend=backend),warm_queries=0)
                e.write_json(out,ranks[backend])
            assert ranks[backend]["quality_valid"]
            assert ranks[backend]["failures"]["classification_count"]==0
            assert ranks[backend]["failures"]["fallback_sample_count"]==0
            assert ranks[backend]["provider"]["active_backend"]==backend
        print(json.dumps({"dataset":name,"phase":"ranked","semantic_top1":ranks["semantic"]["metrics"]["primary"]["accuracy"],"hash_top1":ranks["hash"]["metrics"]["primary"]["accuracy"]}),flush=True)
        out=BASE/f"{name}-llm-raw.json"
        if out.exists(): raw=e.read_json(out)
        else:
            raw=await e.collect_llm(ds,checkpoint=BASE/f"{name}-llm-checkpoint.jsonl",api_key=os.environ["ANTHROPIC_API_KEY"],base_url=os.getenv("ANTHROPIC_BASE_URL") or None,model=os.getenv("ANTHROPIC_MODEL","deepseek-flash"),timeout=60,concurrency=3)
            e.write_json(out,raw)
        modes={}
        for backend in ("semantic","hash","disabled"):
            out=BASE/f"{name}-{backend}-fusion.json"
            if out.exists(): modes[backend]=e.read_json(out)
            else:
                modes[backend]=await e.replay_dataset(ds,rankings=ranks.get(backend),llm=raw,calibration=cal,backend=backend)
                e.write_json(out,modes[backend])
        spec=importlib.util.spec_from_file_location("u002_legacy_intent",Path(".scratch/U002-baseline-intent.py"))
        old=importlib.util.module_from_spec(spec);sys.modules[spec.name]=old;spec.loader.exec_module(old)
        recognizer=old.IntentRecognizer("offline-replay",clock=lambda:e.FIXED_CLOCK)
        recognizer._embedding_enabled=True
        by_id={x["id"]:x for x in raw["rows"]}; oldrows=[]
        try:
            for case in ds["cases"]:
                captured=by_id[case["id"]]
                async def frozen(message,history):
                    value=dict(captured["parsed"])
                    value["intent"]=old.IntentCategory(value["intent"])
                    return value
                recognizer._llm_recognize=frozen
                result=await recognizer.recognize(case["message"],case["history"])
                oldrows.append({**e.case_fields(case),"prediction":result.intent.value,"source_scores":result.source_scores})
        finally: await recognizer.client.close()
        legacy={"commit":"7a4e22a23d33a0bfa910f19662f2a8e40e429d54","source_sha256":e.file_hash(Path(".scratch/U002-baseline-intent.py")),"raw_llm_sha256":raw["raw_sha256"],"rows":oldrows,"metrics":e.grouped_metrics(oldrows,"prediction"),"errors":[x["id"] for x in oldrows if x["prediction"]!=x["expected_intent"]]}
        out=BASE/f"{name}-legacy-fusion.json"
        if not out.exists(): e.write_json(out,legacy)
        reports[name]={"rankings":ranks,"modes":modes,"legacy":legacy,"llm_failures":raw["failures"]}
        print(json.dumps({"dataset":name,"phase":"fused","llm_failures":raw["failures"],"accuracy":{b:r["metrics"]["all"]["accuracy"] for b,r in modes.items()}}),flush=True)
    h=reports["holdout"]; r=reports["regression"]; dev=e.read_json(BASE/"dev-selection.json")
    rank=h["rankings"]; modes=h["modes"]
    added_hash=sorted(set(r["modes"]["semantic"]["errors"])-set(r["modes"]["hash"]["errors"]))
    added_legacy=sorted(set(r["modes"]["semantic"]["errors"])-set(r["legacy"]["errors"]))
    fault=modes["semantic"]["llm_failure"]
    gates={"valid_real_evidence":all(x["fusion_quality_valid"] for q in reports.values() for x in q["modes"].values()),"raw_macro_f1":rank["semantic"]["metrics"]["primary"]["macro_f1"]>=rank["hash"]["metrics"]["primary"]["macro_f1"],"low_overlap_plus_5pp":rank["semantic"]["metrics"]["low_literal_overlap"]["accuracy"]-rank["hash"]["metrics"]["low_literal_overlap"]["accuracy"]>=.05,"fusion_macro_f1":modes["semantic"]["metrics"]["all"]["macro_f1"]>=modes["hash"]["metrics"]["all"]["macro_f1"],"regression_no_added_errors":not added_hash and not added_legacy,"failure_accuracy":(fault["accuracy"] or 0)>=.9,"failure_coverage":fault["coverage"]>=.5,"warm_p95":dev["selected"]["semantic"]["performance"]["warm_queries"]["p95_ms"]<=500,"no_live_llm_failures":not any(v["llm_failures"] for v in reports.values())}
    result={"status":"candidate","gates":gates,"all_gates_pass":all(gates.values()),"added_regression_errors_vs_hash":added_hash,"added_regression_errors_vs_legacy":added_legacy,"holdout_failure":fault,"calibration_sha256":e.fingerprint(cal)}
    e.write_json(BASE/"gate-result.json",result)
    print(json.dumps(result),flush=True)
asyncio.run(main())
