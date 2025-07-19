# Evaluation Framework for Vector‑Based Code Memory

> This doc outlines **how to measure** whether your Qdrant‑backed code memory genuinely boosts Claude Code’s answers—especially when fixing bugs—and **what to implement** so evaluation runs automatically.  Follow the steps below to create a repeatable benchmark, calculate retrieval metrics (Recall\@K, MRR, etc.), and integrate the checks into CI.

---

## 1 · Why Evaluate?

Reliable retrieval is the backbone of Retrieval‑Augmented Generation (RAG): if the right snippets aren’t fed to the LLM, answer quality plummets. Industry guides emphasise that systematic evaluation trumps ad‑hoc manual checks. ([pinecone.io](https://www.pinecone.io/learn/series/vector-databases-in-production-for-busy-engineers/rag-evaluation/?utm_source=chatgpt.com)) Recent research on AI‑assisted bug fixing shows that \~74 % of automatically fixed Rust errors relied on fetching highly similar prior code fragments—a clear win only measurable via rigorous recall metrics. ([github.com](https://github.com/cvangysel/pytrec_eval/blob/master/examples/trec_eval.py?utm_source=chatgpt.com), [arxiv.org](https://arxiv.org/pdf/2310.08837?utm_source=chatgpt.com))

---

## 2 · Build a Benchmark Datasetee ca

1. **Collect query–target pairs** from your issue tracker or code review history.  For each bug report or “how do I…?” question, list the functions/files that developers actually consulted to fix it.
2. Store pairs in YAML or JSONL:
   ```yaml
   - id: Q001
     query: "Prevent NoneType crash in parser"
     relevant:
       - "src/parser.py:validate_input"
       - "src/utils/clean.py:sanitize"
   ```
3. Aim for **≥ 100 diverse queries** to avoid over‑fitting.  Split 80 / 20 into dev/test sets.

> TIP — Use git logs plus `grep -n` to auto‑extract loci touched in each bug‑fixing commit.

---

## 3 · Core Retrieval Metrics

| Metric           | Definition                                              | Why it Matters                                                                                                                                                                                                                                                                           |
| ---------------- | ------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Recall\@K**    | % of queries where *any* relevant doc appears in top K. | Direct signal of “did we find something useful?” ([milvus.io](https://milvus.io/ai-quick-reference/what-is-recall-in-the-context-of-vector-search-results-and-how-is-recall-typically-calculated-when-evaluating-an-ann-algorithm-against-groundtruth-neighbors?utm_source=chatgpt.com)) |
| **Precision\@K** | Relevant docs ÷ K.                                      | Penalises clutter in top results.                                                                                                                                                                                                                                                        |
| **MRR**          | Mean of 1 / rank of first relevant doc.                 | Rewards earlier hits; standard in IR. ([en.wikipedia.org](https://en.wikipedia.org/wiki/Mean_reciprocal_rank?utm_source=chatgpt.com), [evidentlyai.com](https://www.evidentlyai.com/ranking-metrics/mean-reciprocal-rank-mrr?utm_source=chatgpt.com))                                    |
| **MAP / nDCG**   | Averaged precision over ranks or discounted gains.      | Handles multiple relevant docs. ([medium.com](https://medium.com/%40plthiyagu/comprehensive-evaluation-metrics-for-retrieval-augmented-generation-rag-a846ec355c86?utm_source=chatgpt.com), [weaviate.io](https://weaviate.io/blog/retrieval-evaluation-metrics?utm_source=chatgpt.com)) |

**Targets** (adjust per repo size): *Recall\@5 ≥ 0.85*, *MRR ≥ 0.70*.

---

## 4 · Tooling: `pytrec_eval`

- Python wrapper around TREC’s gold‑standard `trec_eval`. ([github.com](https://github.com/cvangysel/pytrec_eval?utm_source=chatgpt.com))
- Supports all ranking metrics out‑of‑box and integrates with numpy & pandas examples. ([weaviate.io](https://weaviate.io/blog/retrieval-evaluation-metrics?utm_source=chatgpt.com))

### Minimal Example

````python
import pytrec_eval, json, qdrant_client, numpy as np

# --- 1. load qrels (ground truth) ---
qrels = json.load(open("benchmark/qrels.json"))  # {query_id: {doc_id: 1, ...}}

# --- 2. run retrieval ---
client = qdrant_client.QdrantClient("localhost", port=6333)
RUN = {}
for qid, query in json.load(open("benchmark/queries.json")).items():
    v = embedder.embed([query])[0]
    hits = client.search(collection_name="code", query_vector=v, limit=10)
    RUN[qid] = {hit.id: (1.0 - hit.score) for hit in hits}  # smaller = better

# --- 3. evaluate ---
metrics = {"recall_5", "recall_10", "mrr", "ndcg_cut_10"}
ev = pytrec_eval.RelevanceEvaluator(qrels, metrics)
print(ev.evaluate(RUN))
``` ([github.com](https://github.com/cvangysel/pytrec_eval/blob/master/examples/trec_eval.py?utm_source=chatgpt.com))

---

## 5 · LLM‑as‑a‑Judge Extension
Ground truth can be fuzzy (e.g., similar helper functions not listed). A 2024 survey details how **LLMs can grade relevance** with high inter‑annotator agreement when given a schema. ([arxiv.org](https://arxiv.org/abs/2411.15594?utm_source=chatgpt.com))  Prompt Claude:
````

System: You are a strict IR judge. User: Is the following snippet relevant to fixing "Prevent NoneType crash in parser"? \n ...&#x20;

````
Aggregate Yes/No votes across K results to compute **LLM‑judged recall**.

---

## 6 · CI Integration
Embed evaluation into GitHub Actions so every PR shows retrieval drift:
```yaml
jobs:
  rag_eval:
    runs-on: ubuntu-latest
    services:
      qdrant:
        image: qdrant/qdrant:v1.9.0
        ports: ["6333:6333"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: '3.11'}
      - run: pip install -e .[dev]
      - run: python scripts/build_index.py
      - run: pytest tests/eval --cov --cov-fail-under=90
````

Metrics can be pushed to Prometheus for historical dashboards. ([medium.com](https://medium.com/%40zilliz_learn/how-to-evaluate-a-vector-database-86dfdcc67d9b?utm_source=chatgpt.com))

---

## 7 · Advanced Evaluation Tracks

1. **Latency & Throughput** — use ANN‑Benchmark harness to log p95 latency under load. ([medium.com](https://medium.com/%40zilliz_learn/how-to-evaluate-a-vector-database-86dfdcc67d9b?utm_source=chatgpt.com))
2. **Answer Fidelity** — pair retrieval with CLAUDE answer accuracy tests using ground‑truth answers.
3. **Embeddings Ablation** — compare OpenAI `text-embedding‑3-small` to local `bge-large` via the same benchmark. ([platform.openai.com](https://platform.openai.com/docs/guides/embeddings?utm_source=chatgpt.com))

---

## 8 · Implementation Checklist

-

---

## 9 · Key Takeaways

A vector database is only as useful as its retrieval quality. Systematic evaluation with **Recall\@K, MRR, and optional LLM judging** ensures that Claude Code sees the right context, directly boosting its bug‑fix suggestions and reducing developer friction.
