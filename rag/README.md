## Search thresholds

`edu_rag.search.search_knowledge_base` filters results with two
thresholds. Both live in `edu_rag/search.py` as module constants:

| Constant | Default | What it does |
|---|---|---|
| `MIN_SCORE` | `0.30` | Absolute cosine floor. Hits below this are dropped. |
| `MIN_RELATIVE` | `0.5` | Hits below `top_score × MIN_RELATIVE` are also dropped. |

### Calibration

Measured against the 590-fragment `edu_materials/parsed` corpus with
**GigaChat `Embeddings`** (`ai-sage/Giga-Embeddings-instruct-3B`,
2048 dims) via the school-hub router, 2026-10-21:

| Query | Class | Top-1 | Top-5 range |
|---|---|---|---|
| `квантовая хромодинамика` | unrelated | 0.18 | 0.16 – 0.18 |
| `рецепт борща с говядиной` | unrelated | ~0.20 | ~0.15 – 0.20 |
| `навыки разработчика программного обеспечения` | related | 0.44 | 0.42 – 0.44 |
| `трудовые функции и необходимые умения` | related | 0.44 | 0.42 – 0.44 |
| `профессиональный стандарт` | related | 0.43 | 0.39 – 0.43 |

The distributions do not overlap. `0.30` sits in the gap with margin on
both sides. With these defaults, `квантовая хромодинамика` returns
`(no results)` — the acceptance criterion "empty result, not a random
fragment" holds.

### When to re-calibrate

Re-run the calibration if:

- the embedding model changes (`OPENAI_EMBEDDING_MODEL`),
- the corpus language or domain shifts,
- GigaChat publishes a new embeddings model with a different
  similarity distribution,
- retrieval quality complaints arrive from ALA integration.

### How to re-calibrate

The CLI supports `--real` for running against the backend's
OpenAI-compatible provider. From `backend/` (so `app` is importable) and
with `.env` sourced:

```bash
poetry run python -m edu_rag.cli search \
  --parsed-dir ../edu_materials/parsed \
  --query "<unrelated query>" \
  --top-k 5 --real --min-score 0.0 --min-relative 0.0

poetry run python -m edu_rag.cli search \
  --parsed-dir ../edu_materials/parsed \
  --query "<relevant query>" \
  --top-k 5 --real --min-score 0.0 --min-relative 0.0
```

### Public API

`search_knowledge_base(query, provider, store, *, source_type, top_k,
min_score, min_relative)` returns `list[SearchHit]`.

`SearchHit` fields:

| Field | Source | Purpose |
|---|---|---|
| `doc_id` | parser `document.json` | stable identifier across runs |
| `ordinal` | parser | chunk position within the document |
| `kind` | parser | `text` or `table` |
| `section_title` | parser | hierarchical section (e.g. `Пояснительная записка > 1.1. Актуальность`) |
| `page_number` | parser (PDF only) | for citation |
| `content` | parser | the fragment text |
| `score` | store | cosine similarity, 0.0–1.0 |
| `metadata["document_title"]` | loader | human-readable title |
| `metadata["source_type"]` | loader | `fgos` / `prof_stand` / `edu_programs` |

Consumers (ALA) map `SearchHit` onto their own DTOs. `edu_rag` does not
own the ALA-side shapes, so no `SearchResult` DTO exists here — adding
one would create two contracts to keep in sync.

### Source-type derivation

`load_chunks_from_json` derives `source_type` from the leading path
segment of `source_path`, but **only** if that segment is one of
`fgos`, `prof_stand`, `edu_programs`. Anything else — a filename with no
directory, `origs/` from a parser run against a corpus subdirectory, an
unexpected folder — yields `source_type="unknown"`, which a corpus
filter will not match.

If you ran the parser against a corpus subdirectory
(`python -m edu_parser.cli --input edu_materials/fgos ...`), pass
`source_type="fgos"` explicitly:

```python
chunks = load_chunks_from_json(path, source_type="fgos")
```
