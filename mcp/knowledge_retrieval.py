"""小型中英知识库的词法候选；不依赖分词模型或查询/文档映射。"""
from collections import Counter
import math
import re
import unicodedata


def lexical_terms(text: str) -> list[str]:
    """中文相邻双字与英文/数字词；不跨标点、空白拼接。"""
    terms = []
    for word in re.findall(r"[\u3400-\u9fff]+|[a-z0-9]+", unicodedata.normalize("NFKC", text).lower()):
        if "\u3400" <= word[0] <= "\u9fff":
            terms.extend(word[i:i + 2] for i in range(len(word) - 1))
        else:
            terms.append(word)
    return terms


def lexical_ranking(query: str, records: list[dict]) -> list[tuple[str, float]]:
    """BM25（k1=1.2、b=0.75）；标题出现计两次，只返回有词法证据的片段。"""
    query_terms = set(lexical_terms(query))
    if not records or not query_terms:
        return []
    counters = [Counter(lexical_terms(row["title"] + "\n" + row["title"] + "\n" + row["content"]))
                for row in records]
    frequencies = Counter(term for counts in counters for term in counts)
    mean_length = sum(sum(counts.values()) for counts in counters) / len(counters) or 1
    ranked = []
    for row, counts in zip(records, counters):
        matched = query_terms & counts.keys()
        if not matched:
            continue
        length = sum(counts.values())
        score = sum(
            math.log(1 + (len(records) - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
            * counts[term] * 2.2 / (counts[term] + 1.2 * (0.25 + 0.75 * length / mean_length))
            for term in matched
        )
        ranked.append((row["chunk_id"], score))
    return sorted(ranked, key=lambda item: (-item[1], item[0]))


def fuse_rankings(vector: list[dict], lexical: list[tuple[str, float]], records: list[dict], top_k: int) -> list[dict]:
    """按片段身份做加权 RRF；不同量纲的向量距离/BM25 不直接相加。"""
    if not lexical:
        return vector[:top_k]
    by_id = {row["chunk_id"]: row for row in records}
    by_id.update({row["chunk_id"]: row for row in vector})
    scores: dict[str, float] = {}
    lexical_scores = dict(lexical)
    vector_scores = {row["chunk_id"]: row["score"] for row in vector}
    # 中文词法权重 2，既有向量权重 1；10 平滑排名，均为固定检索参数。
    for weight, ids in ((1, [row["chunk_id"] for row in vector]), (2, list(lexical_scores))):
        for rank, chunk_id in enumerate(ids, 1):
            scores[chunk_id] = scores.get(chunk_id, 0) + weight / (10 + rank)
    return [
        {**by_id[key], "score": round(scores[key], 6), "score_type": "hybrid_rrf",
         "vector_score": vector_scores.get(key), "lexical_score": round(lexical_scores.get(key, 0), 6)}
        for key in sorted(scores, key=lambda key: (-scores[key], key))[:top_k]
    ]
