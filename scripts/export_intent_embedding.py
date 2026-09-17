"""Export the pinned BGE checkpoint offline in requirements-embedding-build.txt.

Download is a separate preparation step. This script accepts only the pinned
source checksums and writes runtime assets, a manifest, and numerical evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.intent_embeddings import EmbeddingConfig, OnnxSemanticBackend, manifest_space_id

MODEL_ID = "BAAI/bge-small-zh-v1.5"
REVISION = "7999e1d3359715c523056ef9478215996d62a620"
SOURCE_HASHES = {
    "config.json": "3853a7979202c348751b753e36f579c41d8da7d36af617d3d907e1fc9b441f2a",
    "model.safetensors": "354763b9b1357bc9c44f62c6be2276321081ed2567773608c0d0785b61d5a026",
    "tokenizer.json": "48cea5d44424912a6fd1ea647bf4fe50b55ab8b1e5879c3275f80e339e8fae26",
    "tokenizer_config.json": "e6f3b96db926a37d4039995fbf5ad17de158dfb8f6343d607e4dbaad18d75f5a",
    "special_tokens_map.json": "b6d346be366a7d1d48332dbc9fdf3bf8960b5d879522b7799ddba59e76237ee3",
    "vocab.txt": "45bbac6b341c319adc98a532532882e91a9cefc0329aa57bac9ae761c27b291c",
    "1_Pooling/config.json": "aaa8861589f80c961a03cc86c7eeaef7605c1676b9ab55329d33a304738769c6",
    "sentence_bert_config.json": "84e39fda68ccbff05bfa723ae9c0e70e23e2ec373b76e0f8c6e71af72a693cbf",
    "config_sentence_transformers.json": "940d5f50db195fa6e5e6a4f122c095f77880de259d74b14a65779ed48bdd7c56",
    "modules.json": "84e40c8e006c9b1d6c122e02cba9b02458120b5fb0c87b746c41e0207cf642cf",
    "README.md": "c48a4eeea77f6b1d38b48ec1c5b8d4f86d5550cc43fa345a0db1b2ca1d082369",
}
SAMPLES = [
    "你好", "请问医院几点开门？", "我想预约明天的眼科门诊。", "取消刚才准备的预约。",
    "眼睛痒但没有疼痛，该挂哪个科？", "三个月大的孩子咳嗽。", "我咳嗽已经三个月了。",
    "布洛芬200mg普通片的说明书怎么说？", "报告中的 ALT 是什么意思？", "请给我无障碍路线。",
    "从门诊大厅到眼科诊区。", "还有下午的吗？", "我选第一个号源。", "我并没有呼吸困难，只是在咨询急救常识。",
    "Could you show my appointment?", "我想了解 OTC medicine 的禁忌。", "血糖 5.8 mmol/L，参考区间写的是 3.9-6.1。",
    "请问需要身份证、就诊卡和既往检查材料吗？", "天气和电影推荐", "不确定，请先帮我核对需要的信息。",
    "门诊" * 400, "预约 appointment " * 180,
]


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def export(source: Path, output: Path, manifest_path: Path, evidence_path: Path) -> dict:
    import numpy as np
    import onnx
    import torch
    from transformers import AutoModel, AutoTokenizer

    for name, expected in SOURCE_HASHES.items():
        if sha256(source / name) != expected:
            raise ValueError(f"Pinned source checksum mismatch: {name}")
    torch.set_num_threads(2)
    tokenizer = AutoTokenizer.from_pretrained(str(source), local_files_only=True, trust_remote_code=False)
    model = AutoModel.from_pretrained(str(source), local_files_only=True, trust_remote_code=False,
                                     torch_dtype=torch.float32, attn_implementation="eager").eval()

    class Encoder(torch.nn.Module):
        def __init__(self, base):
            super().__init__()
            self.base = base

        def forward(self, input_ids, attention_mask, token_type_ids):
            hidden = self.base(input_ids=input_ids, attention_mask=attention_mask,
                               token_type_ids=token_type_ids, return_dict=False)[0]
            return torch.nn.functional.normalize(hidden[:, 0], p=2, dim=1)

    output.mkdir(parents=True, exist_ok=True)
    encoded = tokenizer(["门诊挂号", "Please show the appointment"], padding=True, truncation=True,
                        max_length=512, return_tensors="pt")
    with torch.inference_mode():
        torch.onnx.export(Encoder(model).eval(), tuple(encoded[name] for name in
                          ("input_ids", "attention_mask", "token_type_ids")), str(output / "model.onnx"),
                          input_names=["input_ids", "attention_mask", "token_type_ids"],
                          output_names=["sentence_embedding"], opset_version=17,
                          dynamic_axes={name: {0: "batch", 1: "sequence"} for name in
                                        ("input_ids", "attention_mask", "token_type_ids")}
                          | {"sentence_embedding": {0: "batch"}}, dynamo=False)
    onnx.checker.check_model(str(output / "model.onnx"))
    shutil.copyfile(source / "tokenizer.json", output / "tokenizer.json")
    # Model-card licensing/provenance travels with the prepared runtime package.
    shutil.copyfile(source / "README.md", output / "MODEL_CARD.md")
    files = {name: {"sha256": digest, "role": "source"} for name, digest in SOURCE_HASHES.items()}
    files["model.onnx"] = {"sha256": sha256(output / "model.onnx"), "role": "runtime"}
    files["tokenizer.json"]["role"] = "source_and_runtime"
    versions = {name: importlib.metadata.version(name) for name in
                ("torch", "transformers", "tokenizers", "huggingface-hub", "safetensors", "onnx", "onnxruntime", "numpy")}
    manifest = {
        "schema_version": 1, "model_id": MODEL_ID, "revision": REVISION,
        "source_url": f"https://huggingface.co/{MODEL_ID}/tree/{REVISION}",
        "license": "MIT", "dimension": 512, "dtype": "float32", "pooling": "cls",
        "normalization": "l2", "max_tokens": 512, "prefix": "",
        "truncation": "longest_first_right", "opset": 17,
        "export_versions": versions, "files": files,
    }
    manifest["space_id"] = manifest_space_id(manifest)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    backend = OnnxSemanticBackend(EmbeddingConfig(model_dir=output, manifest_path=manifest_path))
    backend.initialize()
    reference = []
    with torch.inference_mode():
        for text in SAMPLES:
            inputs = tokenizer([text], padding=True, truncation=True, max_length=512, return_tensors="pt")
            result = model(**inputs).last_hidden_state[:, 0]
            reference.append(torch.nn.functional.normalize(result, p=2, dim=1)[0].numpy())
    batch_vectors, flags = backend.encode_batch(SAMPLES)
    singles = [backend.encode_batch([text])[0][0] for text in SAMPLES]
    ref, actual = np.asarray(reference), np.asarray(batch_vectors)
    cosines = np.sum(ref * actual, axis=1) / (np.linalg.norm(ref, axis=1) * np.linalg.norm(actual, axis=1))
    batch_error = float(np.max(np.abs(actual - np.asarray(singles))))
    results = [{"sample": index + 1, "text": text if len(text) < 150 else text[:80] + "…",
                "characters": len(text), "cosine": float(cosines[index]), "truncated": flags[index]}
               for index, text in enumerate(SAMPLES)]
    evidence = {"model_id": MODEL_ID, "revision": REVISION, "space_id": manifest["space_id"],
                "python": platform.python_version(), "platform": platform.platform(), "versions": versions,
                "reference": "Transformers AutoModel FP32 eager, CLS + torch L2; local_files_only",
                "sample_count": len(SAMPLES), "minimum_cosine": float(min(cosines)),
                "batch_single_max_abs_error": batch_error, "samples": results,
                "passed": bool(min(cosines) >= 0.999 and batch_error < 1e-5)}
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not evidence["passed"]:
        raise RuntimeError("ONNX reference parity failed; see evidence")
    return {key: value for key, value in evidence.items() if key != "samples"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/models/intent/bge-small-zh-v1.5")
    parser.add_argument("--manifest", type=Path, default=ROOT / "config/intent_embedding_model.json")
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.source_dir, args.output_dir, args.manifest, args.evidence), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
