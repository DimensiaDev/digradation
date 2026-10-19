# parser module for digradation

PDF/DOCX → RAG-chunks for educational materials.

## Install

```bash
poetry install --with dev
```

## Run

```bash
poetry run python -m edu_parser.cli \
    --input ../edu_materials \
    --output ../edu_materials/parsed
```

Options:

- `--limit N` — process only the first N files (useful for a quick check).
- `--prune` — after a **full** run, delete stale doc directories in the
  output root that are no longer produced. Do not combine with `--limit`:
  unrelated directories would be removed.
- `--verbose` / `-v` — DEBUG-level logging.

## Tests

```bash
poetry run pytest -q
```

## Known limitations

- **Scanned PDFs** (e.g. `fgos-noo-2021.pdf`, `fgos-ooo-2021.pdf`,
  `fgos-soo-2026.pdf`) have no text layer. The parser extracts each page
  as a PNG into `images/` but yields zero text chunks. OCR is out of
  scope; use `ocrmypdf` if these files must be indexed.
- **Nested tables** inside DOCX cells are not extracted (#18).
- **Merged cells** in DOCX tables are flattened — `python-docx` reports
  the same cell multiple times, so text repeats across columns (#19).
- **Out-of-order PDF TOC** (bookmarks not sorted by page) can misattribute
  section titles (#17).
- **Concurrent runs** sharing one output root are not synchronised. Use
  one process per output root, or a file lock around
  `process_directory` (#6, #7).
