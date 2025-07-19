# Test Suite for **Claude Indexer**

> **Goal** — Achieve ≥ 90 % code‑coverage while guaranteeing that every subsystem (config, hashing, parser, embeddings, vector‑store, watcher, CLI, service) behaves correctly in isolation **and** in combination.  All tests are written for **pytest ≥ 8.0** and assume the refactor detailed in `refactor.md`.

---

## 1 · Directory Layout

```
tests/
├── __init__.py
├── conftest.py           # Shared fixtures
├── unit/
│   ├── test_config.py
│   ├── test_hashing.py
│   ├── test_parser.py
│   ├── test_embeddings.py
│   ├── test_vector_store.py
│   └── test_cli.py
├── integration/
│   ├── test_indexer_flow.py
│   ├── test_watcher_flow.py
│   └── test_delete_event.py
└── e2e/
    └── test_end_to_end.py
```

---

## 2 · `conftest.py` (Key Fixtures)

```python
import os
import shutil
import tempfile
from pathlib import Path
from contextlib import contextmanager

import pytest
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams

from claude_indexer.config import Settings
from claude_indexer.embeddings.openai import OpenAIEmbedder
from claude_indexer.embeddings.local import LocalEmbedder
from claude_indexer.storage.qdrant import QdrantStore

# ---------------------------------------------------------------------------
# Temporary repo fixture -----------------------------------------------------
# ---------------------------------------------------------------------------

@pytest.fixture()
def temp_repo(tmp_path_factory):
    repo_path = tmp_path_factory.mktemp("sample_repo")
    (repo_path / "foo.py").write_text("""
    def add(x, y):
        """Return sum"""
        return x + y
    """)
    (repo_path / "bar.py").write_text("from foo import add\nprint(add(1, 2))\n")
    yield repo_path

# ---------------------------------------------------------------------------
# Qdrant in‑memory fixture ----------------------------------------------------
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def qdrant_client():
    client = QdrantClient("localhost", port=6333)
    # create default collection once per session
    if "test_collection" not in [c.name for c in client.get_collections().collections]:
        client.recreate_collection(
            collection_name="test_collection",
            vectors_config=VectorParams(size=1536, distance=Distance.COSINE),
        )
    yield client
    # no teardown — persist for debugging

@pytest.fixture()
def qdrant_store(qdrant_client):
    return QdrantStore(client=qdrant_client, collection="test_collection")

# ---------------------------------------------------------------------------
# Mock embedder fixture ------------------------------------------------------
# ---------------------------------------------------------------------------

class DummyEmbedder(LocalEmbedder):
    """Fast, deterministic embeddings for tests."""

    def embed(self, texts):
        import numpy as np
        return [np.ones(1536, dtype="float32") * (i + 1) for i, _ in enumerate(texts)]

@pytest.fixture()
def embedder():
    return DummyEmbedder()
```

---

## 3 · Unit Tests Examples

### 3.1 `test_hashing.py`

```python
from pathlib import Path
from claude_indexer.hashing import file_sha256, IndexState

def test_hash_consistency(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("print('hi')")
    h1 = file_sha256(f)
    h2 = file_sha256(f)
    assert h1 == h2


def test_index_state_roundtrip(tmp_path):
    state_file = tmp_path / "state.json"
    state = IndexState(path=state_file)
    state["foo.py"] = "deadbeef"
    state.persist()
    state2 = IndexState(path=state_file)
    assert state2["foo.py"] == "deadbeef"
```

### 3.2 `test_parser.py`

```python
from pathlib import Path
from claude_indexer.analysis.parser import CodeParser

CPY = """def f():\n    return 42\n"""


def test_python_parser(tmp_path):
    (tmp_path / "m.py").write_text(CPY)
    parser = CodeParser(language="python")
    entities, relations = parser.parse(tmp_path / "m.py")
    assert any(e.name == "f" for e in entities)
    assert relations == []
```

### 3.3 `test_embeddings.py`

```python
from claude_indexer.embeddings.local import LocalEmbedder


def test_embedding_shape():
    emb = LocalEmbedder(model_name="intfloat/instructor-xl")
    vec = emb.embed(["hello world"])[0]
    assert len(vec) == 1536
    assert vec.dtype == "float32"
```

### 3.4 `test_vector_store.py`

```python
import numpy as np
from claude_indexer.storage.qdrant import QdrantStore


def test_upsert_and_search(qdrant_store):
    vec = np.random.rand(1536).astype("float32")
    payload = {"path": "foo.py", "line": 1}
    qdrant_store.upsert([("id1", vec, payload)])

    hits = qdrant_store.search(vec, top_k=1)
    assert hits[0].id == "id1"
    assert hits[0].payload["path"] == "foo.py"
```

### 3.5 `test_cli.py`

```python
from click.testing import CliRunner
from claude_indexer.cli import cli


def test_cli_index_command(temp_repo, monkeypatch, embedder, qdrant_store):
    """Monkey‑patch Indexer deps so the command is side‑effect‑free."""
    monkeypatch.setattr("claude_indexer.cli.Indexer", lambda *a, **kw: None)
    runner = CliRunner()
    result = runner.invoke(cli, ["index", str(temp_repo)])
    assert result.exit_code == 0
```

---

## 4 · Integration Tests

### 4.1 `test_indexer_flow.py`

```python
from claude_indexer.indexer import Indexer


def test_full_index_flow(temp_repo, embedder, qdrant_store):
    idx = Indexer(embedder=embedder, store=qdrant_store)
    idx.index_project(temp_repo)

    # verify vectors for each code chunk
    assert qdrant_store.count() >= 2  # foo.py + bar.py
```

### 4.2 `test_watcher_flow.py`

```python
import asyncio
from heiwatch import astart  # hypothetical wrapper around watchdog‑async
from claude_indexer.watcher.handler import Watcher

async def test_async_watch_flow(temp_repo, embedder, qdrant_store):
    watcher = Watcher(repo=temp_repo, embedder=embedder, store=qdrant_store)
    task = asyncio.create_task(watcher.run())

    # modify a file
    p = temp_repo / "foo.py"
    p.write_text("def add(x, y): return x + y + 1\n")

    await asyncio.sleep(1)  # allow watcher debounce + process
    await watcher.stop()
    task.cancel()

    assert qdrant_store.count() >= 2  # updated chunks present
```

### 4.3 `test_delete_event.py`

```python
from claude_indexer.indexer import Indexer

def test_delete_vector_cleanup(temp_repo, embedder, qdrant_store):
    idx = Indexer(embedder=embedder, store=qdrant_store)
    idx.index_project(temp_repo)

    # delete a file and re‑index incrementally
    (temp_repo / "foo.py").unlink()
    idx.index_project(temp_repo)

    payloads = [pt.payload.get("path") for pt in qdrant_store.search_by_payload()]
    assert "foo.py" not in payloads
```

---

## 5 · End‑to‑End Test (`e2e/test_end_to_end.py`)

```python
from subprocess import run, PIPE
from pathlib import Path


def test_cli_end_to_end(temp_repo, qdrant_store):
    cli_path = Path(__file__).parent.parent.parent / "claude_indexer" / "main.py"
    result = run(["python", cli_path, "index", str(temp_repo)], stdout=PIPE, stderr=PIPE)
    assert result.returncode == 0
    assert qdrant_store.count() >= 2
```

> **Note** — `qdrant_store.count()` is a helper added for tests only, wrapping `client.count(collection)`.

---

## 6 · Coverage & CI

- **Coverage** — Add to **pyproject.toml**:
  ```toml
  [tool.coverage.run]
  source = ["claude_indexer"]
  branch = true
  omit = ["*/__init__.py", "*/logging.py"]
  ```
- CI (GitHub Actions):
  ```yaml
  jobs:
    test:
      runs-on: ubuntu-latest
      services:
        qdrant:
          image: qdrant/qdrant:v1.9.0
          ports: ["6333:6333"]
      steps:
        - uses: actions/checkout@v4
        - uses: actions/setup-python@v5
          with:
            python-version: '3.11'
        - run: pip install -e .[dev]
        - run: pytest --durations=10 --cov --cov-fail-under=90
  ```

---

## 7 · Running Locally

```bash
pip install -e .[dev]           # includes pytest, coverage, qdrant‑client
pytest -q                       # run all tests
pytest tests/unit               # unit only
pytest -k "watch_flow" -vv      # single test
coverage html && open htmlcov/index.html
```

---

## 8 · Future Test Extensions

1. **Fuzz‑testing the parser** with Hypothesis to generate random syntactically‑valid code snippets.
2. **Load testing** the vector store bulk‑upsert via Locust.
3. **Chaos tests** — kill OpenAI mock server mid‑batch, ensure retries succeed.
4. **Contract tests** for MCP Graph API using `pytest‑scheme` schema validation.

---

> **Next step**: Integrate these tests into the Phase I pipeline (see `next_step.md`).  Aim for green CI on every PR before merging into `main`.  🚀
