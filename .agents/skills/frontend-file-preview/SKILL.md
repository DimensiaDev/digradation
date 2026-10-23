---
name: frontend-file-preview
description: Conventions for the inline file preview in the digradation frontend — PDF, image, docx and xlsx rendering, lazy loading of docx-preview/xlsx/hyperformula, HyperFormula cross-sheet evaluation, and the upload polling handshake. Use when touching file-preview or attachment upload code.
---

# File preview and attachment upload — digradation conventions

Covers `shared/components/drag-n-drop/file-preview.component.ts`, the chat files panel, and
`AttachmentUploadService`.

## Supported formats

| Type | Preview path |
|---|---|
| PDF | object/iframe embed of the presigned S3 URL |
| PNG, JPEG | `<img>` against the presigned S3 URL |
| DOCX | `docx-preview`, lazily imported |
| XLSX | `xlsx` (SheetJS) parsed and rendered as a sheet grid |

Backend-side accepted upload types: `.pdf`, `.png`, `.jpeg`, `.jpg`, up to
**30 MB**, at most **3 concurrent uploads** (`MAX_FILE_SIZE`, `ACCEPTED_EXTENSIONS`,
`MAX_CONCURRENT_UPLOADS` in `core/models/models.ts`).

The DOCX and XLSX renderers are ahead of the upload allowlist on purpose: the backend
accepts neither today, and both are kept because the moment a feature emits or accepts
one, the preview already works. **Every accepted extension must have a preview kind**,
and a test asserts exactly that, so adding a type to `ACCEPTED_EXTENSIONS` without a
renderer fails the suite rather than shipping a file with no way to open it.

## Heavy dependencies must stay lazy

```typescript
const { renderAsync } = await import('docx-preview');
const XLSX = await import('xlsx');
const { HyperFormula } = await import('hyperformula');
```

Promoting any of these to a static top-level import pulls it into the eager bundle for every
user, including those who never open a preview. Keep the dynamic `import()`.

## The DOCX renderer is an unsanitized HTML sink, and is hardened twice

`docx-preview` builds DOM imperatively — no template binding, no `DomSanitizer` — so both `href`
and the stylesheet it assembles come from the file, which arrived from outside the organisation.
`renderDocx` renders into a **shadow root** and then calls `hardenRenderedDocument` on it, before
the container carrying that tree is appended:

- The shadow root confines the stylesheet *selectors*. The renderer interpolates document style
  and font names into CSS rules *without escaping*, so a name containing `}` closes its rule and
  opens a global one. Inside a shadow tree a rule that stays there reaches nothing outside it.
  **Do not "fix" this by removing the `<style>` elements** — that is the document's own
  formatting, and the preview would render as plain text. The scope is the fix, not the deletion.
- `:host` is *not* confined, and neither is a fetch. Inside the shadow tree `:host` matches the
  preview's own container, so a document could move or hide the dialog controls; and a rule the
  document synthesised can hold `@import` or `url(https://…)`, which the browser fetches while
  the preview is only being looked at. `hardenRenderedDocument` therefore rewrites each generated
  `<style>`: `@import` removed, `:host` replaced by an attribute selector nothing carries, and
  every non-`data:` `url()` pointed at `about:invalid`. It runs while the tree is still detached —
  a stylesheet in a detached tree is not applied and its `@import` was never fetched — so that
  ordering is the guard. A `data:` URL is kept: that is how an embedded font arrives.
- `hardenRenderedDocument` removes script/frame/meta-class elements and strips the `href` of any
  link whose scheme is not http/https/mailto/tel. A shadow boundary is **not** a script sandbox,
  so a `javascript:` link left alone would still execute. A fragment-only target (a bookmark, a
  table-of-contents entry) is a place in the document, not an address: it keeps its `href` and
  gets no `target`, or the table of contents opens the app in a second tab.

`sanitizePreviewCss` and `hardenRenderedDocument` are exported so the spec can test them on real
injected CSS; a render alone cannot prove a rewrite ran on input it never produced.

Anything else that appends rendered HTML to the DOM belongs on this list too: it is a sink and
owes the same treatment.

## XLSX previews need a formula engine

A workbook may price its rows with **formulas** rather than literal values. Rendering
cells without evaluating formulas shows blanks where numbers should be. HyperFormula is
configured to evaluate across sheets, because a spreadsheet that keeps its inputs on one
sheet and references them from another is the normal case.

When changing the renderer, verify a computed cell actually displays a number, not an
empty cell or a raw formula string.

## Sheet tabs and the active pill

XLSX previews show one tab per sheet with a sliding active-pill indicator. The pill position
is animated, and the indicator must stay correct when a sheet is switched and when the
preview is re-rendered for a different file. `first_position`-style selection state is per
preview instance, not global.

## Download versus preview

Previews read from a **presigned S3 URL** returned by the backend. The download action
uses a separate presigned URL. Both expire, so a URL held in component state can go stale;
re-fetch rather than caching a long-lived URL.

## Upload handshake and polling

Upload is a two-step flow: the backend issues presigned S3 upload parameters, the browser
uploads directly to object storage, then the client confirms. Status is polled rather than pushed:

```typescript
const STATUS_POLL_INTERVAL_MS = 2000;
const STATUS_POLL_LIMIT = 150;
```

`AttachmentUploadService` polls upload status on that interval up to that limit, then gives
up. Keep these constants; they bound the request storm and the user-visible wait.

Server-side, `process_attachment` and `process_document` run on the `default` TaskIQ queue and
write progress keys that this polling reads. Progress values are `completed` and `error`.

## Result polling

Generation results are polled separately in the chat component:

```typescript
const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 5 * 60 * 1000;
```

A 5-minute timeout followed by a visible state is intentional. Do not extend it silently to
hide a stuck worker.

## Gotchas

- Failure is a **normal, expected state** in this UI, not an exception. Every preview path
  needs a visible error state alongside the skeleton and empty states.
- Delete a session while a result is in flight and the backend discards the result via a
  tombstone key; the frontend may still receive one poll response before that happens, so
  guard against updating destroyed state.
- Markdown rendering in chat uses `marked` plus `marked-highlight` and `highlight.js`, and is
  unrelated to file previews even though both live in the chat view.

## Verify

```bash
npm --prefix frontend run lint
npm --prefix frontend test
npm --prefix frontend run build

# end-to-end check needs the real stack, since previews read from S3
docker compose up -d --build
```

Then exercise: upload a PDF and a PNG, download one, reopen the preview, and — for a
workbook — switch between its sheets.
