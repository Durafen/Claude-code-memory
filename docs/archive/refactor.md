# Refactor Plan for **Universal Indexer**

> **Objective** — Re‑architect `indexer.py` into a maintainable, modular codebase with zero duplication, clear separation of concerns, and a future‑proof path for multi‑language support and scale.

---

## 1 · Pain Points Observed

| Issue                               | Evidence                                                                                                   |
| ----------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| **File ≈ 2 000 + LOC monolith**     | Single script mixes CLI, analysis, network, async watching, service logic                                  |
| **Duplicate classes & functions**   | `IndexingEventHandler`, `IndexingService`, `GitHooksManager` appear twice fileciteturn2file4turn2file6 |
| **Debounce via per‑file **``        | Memory/thread leakage risk fileciteturn2file4                                                           |
| **Per‑event Indexer instantiation** | O(N × M) blow‑up during watch mode fileciteturn2file4                                                   |
| **No retry/back‑off wrapper**       | Network fragility around OpenAI & Qdrant calls fileciteturn2file1                                       |
| **Mixed concerns in **``            | Hashing, parsing, embeddings, graph I/O, CLI printing all in one class fileciteturn2file1               |

---

## 2 · Refactor Goals

1. **Zero Duplication** — Single source of truth for every class/function.
2. **Package‑Level Modularity** — Small files, each owning a single domain.
3. **Pluggable Components** — Switch parsers, embedders, or vector stores via dependency injection.
4. **Async & Resource‑Safe** — Event loop–based file watching with bounded queues.
5. **Extensible CLI** — Click‑based command group, auto‑generated help, unit‑testable.
6. **Comprehensive Tests & Docs** — 80 %+ coverage; `docs/architecture.md` diagrams.

---

## 3 · Proposed Package Layout

```
claude_indexer/
├── __init__.py
├── cli.py               # Click command groups: index, watch, service, hooks
├── config.py            # Load/merge settings + env, schema‑validated (pydantic)
├── logging.py           # Structured logging (loguru), central config
├── hashing.py           # SHA‑256 + state persistence
├── analysis/
│   ├── __init__.py
│   ├── parser.py        # Tree‑sitter / Jedi adapters
│   └── entities.py      # Dataclasses for Entity / Relation
├── embeddings/
│   ├── __init__.py
│   ├── openai.py        # Remote embedder with retry/back‑off
│   └── local.py         # Instructor‑XL / bge adapters
├── storage/
│   ├── __init__.py
│   ├── qdrant.py        # Vector + metadata CRUD
│   └── graph.py         # MCP client wrapper
├── watcher/
│   ├── __init__.py
│   ├── debounce.py      # Async queue + coalescing
│   └── handler.py       # FileSystemEventHandler impl
├── service.py           # Background multi‑project supervisor
├── git_hooks.py         # Install/verify/remove hooks
└── main.py              # `python -m claude_indexer` entry point
```

> **Rationale** — Each directory groups interchangeable components (e.g., swap `storage.qdrant` with `storage.milvus` without touching `analysis`).

---

## 4 · Key Refactor Steps

1. **Extract Config Loader**
   - Move `load_settings()` into `config.py` with `pydantic.BaseSettings` for validation and env overrides.
2. **Split Entity Logic**
   - Create `analysis.entities` with `@dataclass` `Entity` and `Relation`; remove mutable dicts.
3. **Parser Abstraction**
   - `analysis.parser.CodeParser` exposes `parse(Path) -> list[Entity], list[Relation]`; default impl uses Tree‑sitter + Jedi.
4. **Embedder Strategy**
   - `embeddings.base.Embedder` interface; plug‑in OpenAI or local model.
   - Centralized retry with exponential back‑off / jitter.
5. **Vector Store Adapter**
   - `storage.vector_store.VectorStore` interface; default `QdrantStore` handles upserts, deletes, similarity search.
6. **Domain Service: Indexer**
   - `Indexer` orchestrates parser → embedder → store; stateless, single‑file or batch.
7. **Async File Watcher**
   - Replace per‑file `Timer` with `asyncio.Queue` + single consumer; coalesce rapid events.
8. **Background Service**
   - `service.py` spins watchers in tasks; graceful shutdown via `asyncio.run(main())`.
9. **CLI Overhaul**
   - Use **Click** groups: `index`, `watch`, `service`, `hooks`.
   - Flags validated by Click; reuse common options via decorators.
10. **Tests & Linters**
    - Pytest fixtures for temporary repos; coverage for hashing, parser, watcher flow.
    - Ruff/Black enforced in CI.
11. **Docs Generation**
    - Autodoc with MkDocs Material; architecture diagrams via Mermaid.

---

## 5 · Duplicate Removal Checklist

| Duplicate                          | Action                                                                               |
| ---------------------------------- | ------------------------------------------------------------------------------------ |
| `IndexingEventHandler` (2×)        | Keep one in `watcher/handler.py`; delete duplicates fileciteturn2file4turn2file6 |
| `IndexingService` (2×)             | Move to `service.py`; consolidate methods fileciteturn2file2turn2file7           |
| `GitHooksManager` (2×)             | Extract to `git_hooks.py`; unify installation logic fileciteturn2file2turn2file8 |
| CLI `argparse` parsing blocks (2×) | Replace with single Click entry point in `cli.py`                                    |

---

## 6 · Future‑Proofing Extensions

- **Multi‑Language Support** — Add parsers (`analysis.parser.ts`, `analysis.parser.go`) implementing same interface.
- **Plugin Registry** — `entry_points` in `pyproject.toml` allow third‑party parsers/embedders.
- **Batch Processing API** — Expose `POST /index` endpoint (FastAPI) for CI pipelines.
- **Streaming Vectors** — Support `storage.vector_store.AsyncVectorStore` once Qdrant gRPC streaming matures.
- **Observability Hooks** — Prometheus metrics via `logging.py`; tracing with OpenTelemetry.

---

## 7 · Milestone Timeline

1. **Week 1** — Config extraction, logging, test harness skeleton.
2. **Week 2** — Duplicate removal, package skeleton, migrate hashing & parser.
3. **Week 3** — Embedder abstraction, Qdrant adapter, Indexer core.
4. **Week 4** — Async watcher + service, Click CLI, initial docs.
5. **Week 5** — Full unit tests, CI job, release `v0.2.0` on PyPI.

---

## 8 · Outcome

A lean, plugin‑oriented package that any team can install (`pip install claude‑indexer`) and extend, with separate modules for config, parsing, embeddings, storage, watching, and CLI orchestration.  This sets the groundwork for scaling to polyglot codebases and evolving vector‑store tech, while ensuring the codebase stays approachable for new contributors.
