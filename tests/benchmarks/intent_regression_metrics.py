"""One fresh pass over the already-used 186-case set; regression, never a new holdout."""
import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from core.intent_recognizer import IntentRecognizer
from evaluation.evaluator import IntentEvaluator, IntentTestCase


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")


async def run(output):
    output.mkdir(parents=True,exist_ok=False)
    source=Path("evaluation/cases/semantic_intent/holdout.json")
    dataset=json.loads(source.read_text(encoding="utf-8"))
    assert len(dataset["cases"])==186
    save(output/"inputs.json",dataset)
    clock=datetime.fromisoformat("2026-09-16T10:00:00+08:00")
    recognizer=IntentRecognizer(api_key=os.environ["ANTHROPIC_API_KEY"],base_url=os.getenv("ANTHROPIC_BASE_URL"),model=os.environ["ANTHROPIC_MODEL"],clock=lambda:clock)
    config={"started_at":datetime.now(timezone.utc).isoformat(),"model":os.environ["ANTHROPIC_MODEL"],"thinking":os.getenv("MEDIPET_THINKING"),"use":"previously used set; current-version regression only", "fresh_recognizer":True,"learn_calls":0,
            "input_sha256":hashlib.sha256(source.read_bytes()).hexdigest(),
            "script_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "sources":{p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in ["core/intent_recognizer.py","core/intent_embeddings.py","evaluation/evaluator.py"]}}
    save(output/"configuration.json",config)
    calls=[0]
    original=recognizer.client.messages.create

    async def counted(**kwargs):
        calls[0]+=1
        return await original(**kwargs)

    recognizer.client.messages.create=counted
    cases=[IntentTestCase(id=c["id"],message=c["message"],expected_intent=c["expected_intent"],context={"history":c["history"]}) for c in dataset["cases"]]
    evaluator=IntentEvaluator(recognizer)
    try:
        # Checkpoint each block but compute the final macro-F1 over all predictions.
        details=[]
        for start in range(0,len(cases),20):
            result=await evaluator.evaluate(cases[start:start+20])
            details.extend(result["cases"])
            save(output/f"block-{start:03d}.json",result)
            print(json.dumps({"completed":len(details),"correct":sum(c["predicted"]==c["expected"] for c in details)}),flush=True)
        labels=sorted({d["expected"] for d in details}|{d["predicted"] for d in details if d["predicted"] is not None})
        per_class={}
        for label in labels:
            tp=sum(c["expected"]==c["predicted"]==label for c in details)
            fp=sum(c["predicted"]==label and c["expected"]!=label for c in details)
            fn=sum(c["expected"]==label and c["predicted"]!=label for c in details)
            precision=tp/(tp+fp) if tp+fp else 0
            recall=tp/(tp+fn) if tp+fn else 0
            per_class[label]={"precision":precision,"recall":recall,"f1":2*precision*recall/(precision+recall) if precision+recall else 0}
        correct=sum(c["expected"]==c["predicted"] for c in details)
        summary={"total":186,"observed":len(details),"correct":correct,"accuracy":correct/186,"macro_f1":sum(x["f1"] for x in per_class.values())/len(labels),"per_class":per_class,
                 "failures":[c for c in details if c["predicted"]!=c["expected"]],"call_failures":sum(c.get("call_failed",False) for c in details)}
        save(output/"summary.json",summary)
        print(json.dumps({k:v for k,v in summary.items() if k not in ['per_class','failures']}),flush=True)
    finally:
        config.update(finished_at=datetime.now(timezone.utc).isoformat(),model_calls=calls[0],embedding=recognizer.embedding_status(),template_revision=recognizer._template_revision)
        save(output/"configuration.json",config)
        await recognizer.aclose()


if __name__=="__main__":
    asyncio.run(run(Path(sys.argv[1])))
