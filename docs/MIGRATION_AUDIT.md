# Chalk Desktop to Web Migration Audit

Audit date: 2026-07-12

## Result

All 11 desktop work areas have a Web route and a user-operable workflow. The Web implementation uses authenticated REST APIs and persistent Jobs; it does not import Qt windows, workers, dialogs, or local-path controls.

| Area | Web status | Notes |
| --- | --- | --- |
| Authentication | Complete | Register, login, logout, session recovery, user isolation. |
| Literature management | Complete | PDF upload, reimport, delete, browser PDF reader, CSV list export, RAG jump, seven analysis tabs, 2-5 paper comparison. |
| Literature search | Complete | Five providers, diagnostics, year/domain filters, cancellation, result selection, References export, hypothesis handoff, evidence indexing. |
| RAG question answering | Complete | All-document or single-document scope, citations, retry/cancel/error states. |
| Lab records | Complete | Search, create, edit, delete, related paper, AI suggestion Job, evidence indexing. |
| Glossary | Complete | Search, upsert, delete, translation batch save. |
| Hypothesis generation and HITL | Complete | Multi-document and multimodal inputs, prompt polishing, progress recovery, approve/revise/skip, verification and persisted history. |
| Hypothesis library | Complete | Filters, metadata edit, delete, native detail, report/Trace/workflow, lab/modeling/evidence handoffs. |
| Evidence database | Complete for browser-safe workflows | Filters, detail, real relationship graph, search/lab/hypothesis indexing, curated seed import, managed CSV/JSON manifest Job. |
| Multimodal analysis | Complete | Managed upload, clipboard/drop, document images, spreadsheet coverage, partial success, annotation/correction revisions, history, copy/clear. |
| Computational modeling | Complete within the desktop safety boundary | Document/hypothesis/manual sources, editable provenance, structure validation and Three.js, revision history, dry-run ZIP. |
| API settings | Complete | Per-user DashScope, Semantic Scholar, NCBI and Crossref polite-pool credentials; plaintext values are never returned. |

## Deliberate Web Differences

- The PDF reader uses the browser's native reader instead of rebuilding Qt page, zoom, search and print controls.
- Search provider selection is explicit per search. This avoids one user's Web settings changing a process-wide provider switch for other users.
- Long model and import operations are persistent Jobs instead of modal worker dialogs.
- Scientific results are structured, revisioned workspaces rather than editable text boxes with no provenance.
- CSV/XLS/XLSX analysis is handled by the multimodal table workspace. The desktop-only option that converted the first 300 table rows into a pseudo-document is not reproduced because it silently truncated data. A future searchable-dataset feature should index explicit row ranges and return coverage metadata.

## Restricted Or Deferred Operations

These are not missing buttons in the user Web application:

- OCP-Dense `.pkl` and mapping archive import remains a trusted local/admin operation. The legacy reader uses `pickle.load`; accepting arbitrary browser uploads would create a remote-code-execution boundary.
- Registering an arbitrary OC/OCP server-local directory remains an admin/local operation. Browser users must not submit filesystem paths for the server to scan.
- VASP, Materials Studio, HPC queues and remote command execution remain intentionally out of scope. Both Web exports and workflow packages are dry-run only.
- XLSX and Parquet document-list export are not exposed. CSV is the portable browser export; scientific artifacts retain their dedicated ZIP/HTML/JSON formats.
- Desktop global keyboard shortcuts are not copied. Browser and assistive-technology navigation remain authoritative.

## Verification Gates

- Backend unit/API suite.
- Next.js production build and ESLint.
- Playwright desktop and mobile workflows, including the final search, lab and evidence handoffs.
- Dependency audit.
- Legacy regression suite, with every source test mapped in
  [Legacy Test Migration Audit](LEGACY_TEST_MIGRATION.md).
