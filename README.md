# Chalk Web

`chalk-new` is a self-contained Web distribution of Chalk. It can be copied, renamed, and run outside the original desktop repository: the FastAPI backend uses the scientific capabilities bundled under `src/chalk_app`, and runtime data stays under this project's `data/` directory by default.

"Self-contained" means the project does not import source code or read databases from an original Chalk checkout. Python and Node.js dependencies are still installed from their package managers, and network-backed research providers still require network access and, where applicable, credentials.

## Structure

- `frontend/`: Next.js user interface.
- `backend/`: FastAPI routes, user-scoped resources, persistent Web Jobs, and scientific capability adapters.
- `src/chalk_app/`: bundled, Web-compatible scientific core; desktop-only UI code is deliberately excluded.
- `data/`: runtime-only Web database, sessions, API keys, uploads, analysis assets, and logs.
- `scripts/`: local development commands.
- `benchmarks/science125/`: versioned Science 125 manifest; the source PDF is not committed.
- `docs/contracts/`: generated `research-v1` JSON Schema.
- `docs/decisions/`: architecture decisions for migration boundaries and restart behavior.
- `pyproject.toml`: installation metadata for the bundled `chalk_app` package.

See [ADR-001](docs/decisions/001-hypothesis-hitl-web-jobs.md) for the hypothesis/HITL Job model and its restart guarantees. See [ADR-002](docs/decisions/002-scientific-workspaces-and-dry-run-modeling.md) for multimodal provenance, revision history, structure validation, and dry-run export boundaries.
See [ADR-003](docs/decisions/003-browser-import-and-local-path-boundaries.md) for evidence-import security boundaries, [ADR-004](docs/decisions/004-standalone-web-distribution.md) for the standalone distribution boundary, [ADR-005](docs/decisions/005-research-projects-and-science125.md) for the shared research contract and Science 125 boundaries, and [the migration audit](docs/MIGRATION_AUDIT.md) for desktop/Web parity and deliberate differences.

## Current Web Slice

- Cookie-based registration, login, logout, and current-user recovery.
- Browser PDF upload and reimport with type, size, ownership, and PDF-header validation, plus an authenticated browser-native PDF reader and CSV metadata export.
- Persistent SQLite Web Jobs with polling, cancellation, filtering, and restart interruption handling.
- Real literature search through the legacy Crossref, arXiv, Semantic Scholar, DOAJ, and PMC adapters, with per-user provider credentials, References export, hypothesis handoff, and evidence indexing.
- Real RAG question answering over the signed-in user's imported documents, with source chunks and citations.
- Full-document segmented summary, SOP, reaction extraction, translation, PDF image extraction, PubChem structures, and safety lookup.
- Persistent latest analysis results, protected image assets, reaction-to-lab-record flow, glossary batch save, and 2-5 document comparison.
- Full hypothesis generation through the legacy `HypothesisOrchestrator`, with balanced multi-document context, per-document evidence retrieval, automatic verification, and one active hypothesis task per user.
- Persistent HITL review checkpoints with approve, revise, skip, structured criteria, debate stance, JSON edits, cancellation, refresh recovery, and restart interruption handling.
- A filtered hypothesis library and native detail workspace for iterations, critiques, reasoning, debate, HITL history, verification, tree search, and dry-run workflow data.
- Managed CSP-restricted HTML reports, path-sanitized Agent Trace JSON, and bounded workflow ZIP exports. HTML preview runs in an iframe sandbox without same-origin permission.
- Batch multimodal analysis for uploaded images, XLSX, XLS, CSV, and extracted document images, with partial-success Jobs, explicit spreadsheet coverage, linked data points, associations, quantitative results, and user revision history.
- Image zoom, pan, normalized region annotations, read-only paginated table previews, explicit selection of up to ten workbook sheets, structured data-point correction, optimistic revision checks, and explicit selection of corrected multimodal evidence for hypothesis generation.
- Revisioned VASP and Materials Studio modeling workspaces sourced from manual text, full documents, or hypothesis workflow packages, with parameter provenance and server-computed diffs.
- Editable INCAR, KPOINTS, and ordered POTCAR suggestions, validated CIF/POSCAR upload, hypothesis structure-candidate import, interactive Three.js structure inspection, and bounded ZIP exports containing dry-run inputs, provenance, manifests, and risk notices. Chalk never includes POTCAR binaries or submits a calculation.
- User-scoped documents, glossary, lab records, hypotheses, evidence queries, artifacts, development-only server-side provider-key storage, safe manifest import, and an interactive evidence relationship graph. Production ignores plaintext key files and requires process-environment credentials.

## Requirements

- Python 3.14
- Node.js 24.x
- pnpm 11.11.0 through Corepack

## First Run

Open PowerShell in the copied project's root directory, regardless of its directory name or location:

```powershell
Copy-Item backend\.env.example backend\.env
Copy-Item frontend\.env.example frontend\.env.local

cd backend
python -m pip install -r requirements.txt

cd ..\frontend
corepack pnpm install --frozen-lockfile
```

The backend requirements install the bundled `src/chalk_app` package in editable mode through the root `pyproject.toml`; no original Chalk checkout is required. Keep `CHALK_LEGACY_ROOT` unset for normal standalone use. It exists only as an explicit compatibility override for development against an external legacy source tree.

Heavy integrations that degrade gracefully when unavailable are grouped as optional extras in `pyproject.toml` (`agents`, `documents`, `modeling`, and `visualization`). Install only the groups needed for a deployment, for example from `backend/`: `python -m pip install -e "..[agents,modeling]"`.

Replace `CHALK_SESSION_SECRET` in `backend\.env` with a long random local secret before using real data.

## Start

From the copied project's root directory:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\dev.ps1
```

- Frontend: `http://127.0.0.1:3000`
- Backend health: `http://127.0.0.1:8000/api/health`
- OpenAPI: `http://127.0.0.1:8000/docs`

Press `Ctrl+C` in the launch terminal to stop both services.

## Verification

From the project root:

```powershell
cd backend
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests

cd ..
python -m pytest tests/legacy -q
python scripts/generate_research_contract.py --check
python scripts/validate_science125_manifest.py benchmarks/science125/science125-v1.json

cd frontend
corepack pnpm install --frozen-lockfile
corepack pnpm exec playwright install chromium
corepack pnpm build
corepack pnpm lint
corepack pnpm test:e2e
corepack pnpm test:e2e:fullstack
corepack pnpm audit --audit-level=high

cd ..\backend
python -m pip_audit --requirement requirements.txt --progress-spinner off
```

The contract generator must report that both generated artifacts are current. The
manifest validator checks exactly 125 continuous, unique questions, the locked
category counts, item hashes, the source-PDF hash, and the manifest hash.

## Qwen Canary

The real canary calls DashScope for `S125-006`, `S125-043`, and `S125-054`. It
requires a process-only credential and writes each raw provider response,
validated `research-v1` output, and a redacted per-attempt ledger under the
ignored `data/` directory:

```powershell
$env:DASHSCOPE_API_KEY = '<session-only key>'
python scripts/run_science_canary.py `
  --manifest benchmarks/science125/science125-v1.json `
  --output-dir data/canary `
  --summary-path data/canary-summary.json `
  --ledger-path data/canary-ledger.db
Remove-Item Env:DASHSCOPE_API_KEY
```

`--max-total-tokens` defaults to 60,000 and each provider call is capped at
8,192 completion tokens. To enforce a model-price budget as well, pass current
DashScope prices with `--input-cost-per-million-cny`,
`--output-cost-per-million-cny`, and a positive `--max-estimated-cost-cny`.
The runner rejects a cost ceiling when either price is unknown instead of
recording a misleading budget.

For GitHub, create a protected environment named `qwen-canary`, add its
`DASHSCOPE_API_KEY` secret, and define `QWEN_MAX_ESTIMATED_COST_CNY`,
`QWEN_INPUT_COST_PER_MILLION_CNY`, and
`QWEN_OUTPUT_COST_PER_MILLION_CNY` environment variables using the current
DashScope model price. The manual **Qwen Canary** workflow refuses to call the
model without all four values. Its private evidence artifact is retained for 14
days. The normal PR workflow never uses the key or spends model quota.

In development, configured provider keys remain on the backend and are never returned as plaintext; production uses process-environment credentials and ignores the plaintext store. Uploaded files and managed assets are stored under user-specific runtime directories, and server paths are never exposed by API responses. Model-backed document analysis rejects documents above `CHALK_MAX_ANALYSIS_CHARS` rather than silently truncating them. Hypothesis source context is explicitly balanced to 60,000 document characters plus up to 40,000 supplemental characters; any document truncation is returned as a Job warning.
