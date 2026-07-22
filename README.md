# Chalk Web

`chalk-new` is a self-contained Web distribution of Chalk. It can be copied, renamed, and run outside the original desktop repository: the FastAPI backend uses the scientific capabilities bundled under `src/chalk_app`, and runtime data stays under this project's `data/` directory by default.

"Self-contained" means the project does not import source code or read databases from an original Chalk checkout. Python and Node.js dependencies are still installed from their package managers, and network-backed research providers still require network access and, where applicable, credentials.

## Structure

- `frontend/`: Next.js user interface.
- `backend/`: FastAPI routes, user-scoped resources, persistent Web Jobs, and scientific capability adapters.
- `src/chalk_app/`: bundled, Web-compatible scientific core; desktop-only UI code is deliberately excluded.
- `data/`: runtime-only Web database, sessions, API keys, uploads, analysis assets, logs, and the machine-local Science 125 source-context index.
- `scripts/`: local development commands.
- `benchmarks/science125/`: immutable Science 125 manifest plus the hashed `science125-routing-v1` domain overlay; the source PDF is not committed.
- `docs/contracts/`: generated `research-v1` JSON Schema.
- `docs/decisions/`: architecture decisions for migration boundaries and restart behavior.
- `pyproject.toml`: installation metadata for the bundled `chalk_app` package.

See [ADR-001](docs/decisions/001-hypothesis-hitl-web-jobs.md) for the hypothesis/HITL Job model and its restart guarantees. See [ADR-002](docs/decisions/002-scientific-workspaces-and-dry-run-modeling.md) for multimodal provenance, revision history, structure validation, and dry-run export boundaries.
See [ADR-003](docs/decisions/003-browser-import-and-local-path-boundaries.md) for evidence-import security boundaries, [ADR-004](docs/decisions/004-standalone-web-distribution.md) for the standalone distribution boundary, [ADR-005](docs/decisions/005-research-projects-and-science125.md) for the retained `research-v1` contract, [ADR-006](docs/decisions/006-science125-only-general-workbench.md) for the current Science 125-only workbench boundary, [ADR-007](docs/decisions/007-science125-domain-routing-and-rate-governed-retrieval.md) for domain routing and provider limits, and [the migration audit](docs/MIGRATION_AUDIT.md) for desktop/Web parity and deliberate differences.

## Current Web Slice

- Cookie-based registration, login, logout, and current-user recovery.
- Browser PDF upload and reimport with type, size, ownership, and PDF-header validation, plus an authenticated browser-native PDF reader and CSV metadata export.
- Persistent SQLite Web Jobs with polling, cancellation, filtering, and restart interruption handling.
- Real literature search through the legacy Crossref, arXiv, Semantic Scholar, DOAJ, and PMC adapters, with per-user provider credentials, References export, hypothesis handoff, and evidence indexing.
- Real RAG question answering over the signed-in user's imported documents, with source chunks and citations.
- Full-document segmented summary, SOP, reaction extraction, translation, PDF image extraction, PubChem structures, and safety lookup.
- Persistent latest analysis results, protected image assets, reaction-to-lab-record flow, glossary batch save, and 2-5 document comparison.
- Full hypothesis generation through the legacy `HypothesisOrchestrator`, with balanced multi-document context, per-document evidence retrieval, automatic verification, and one active hypothesis task per user.
- Science 125 cross-disciplinary workflow over the authoritative 125-question catalog: server-bound full booklet context, editable context-derived literature queries, human evidence selection, hash-bound page excerpts from owned PDFs, and a dedicated `research-v1` Qwen path for `S125-006`, `S125-043`, and `S125-054`. The other 122 questions remain generation-gated, and the chemistry prompt is never reused.
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

## Science 125 Source Context

The repository does not contain the booklet PDF or its long explanatory text. To enable the complete-question view locally, keep an authorized copy of `sjtu-booklet.pdf` outside Git and build the ignored runtime index:

```powershell
python scripts/build_science125_context.py `
  "C:\\path\\to\\sjtu-booklet.pdf"
```

The builder refuses a different source-PDF SHA-256 and validates all 125 entries before replacing `data\\science125\\science125-context-v1.json`. Without this index the API returns `SCIENCE125_CONTEXT_UNAVAILABLE`; it does not fall back to a short headline. Future Science 125 generation loads this context again on the server, so the browser cannot substitute a shortened question. The page-selection endpoint is authenticated, accepts only files in the signed-in user's managed upload directory, and reads only the requested PDF pages:
`POST /api/documents/{documentId}/page-excerpts` with `{ "pages": [2, 4, 5], "maxChars": 12000 }`.
The dedicated Science 125 input assembler re-extracts those pages and verifies their PDF/text hashes before including the page text. Pilot generation reloads the completed owned search on the server, accepts only reviewed stable evidence IDs, and requires at least three full-text records from two provider families.

## Science 125 Domain Routing and Providers

The authoritative `science125-v1` file preserves the booklet's 125 questions and
official benchmark domains. The separate `science125-routing-v1` overlay adds the
reviewed primary subdomain, cross-domain tags, method profile, dedicated prompt
profile, and API retrieval profile for each question. Its SHA-256 is checked against
the base manifest whenever the catalog is loaded; editing a benchmark domain in the
overlay is rejected.

Science 125 uses the following sequence:

```text
full verified booklet context
-> server-selected domain retrieval profile
-> official API search under the provider limiter
-> researcher evidence review
-> dedicated Science 125 Qwen prompt
```

The provider registry is API-first. It permits arXiv, NCBI, Semantic Scholar,
Crossref, OpenAlex, Europe PMC, INSPIRE HEP, NASA ADS, DBLP, GBIF Literature, OSTI,
DOAJ, and ClinicalTrials.gov. Arbitrary publisher-page scraping, Google Scholar
scraping, and user-supplied URLs are not accepted. User-owned PDF page excerpts are
the supported route for reviewed supplemental full text.

Provider limits are enforced per provider and credential scope, across search,
pagination, details, and retries. The current fixed rules are arXiv at a minimum
three-second interval, NCBI at no more than 3 requests/second without an API key or
10 requests/second with one, and keyed Semantic Scholar at a one-request/second
safety floor. Other providers use official quota headers with a conservative
single-concurrency floor until a stable numeric policy is documented. `Retry-After`
and dynamic remaining/reset headers are honored. Cooldowns and reservations persist
in `science125_provider_rate_state` in `web.db`, so do not delete or replace the
runtime database while a run is active.

### Server credentials

Science 125 provider credentials are process-only. The backend does not read the
per-user `data/api_keys.json` store for these profiles and the profile API returns
only public provider metadata and redacted readiness codes. Keep real values in a
local process environment or a protected deployment secret, never in Git, chat, or
`backend/.env.example`.

The template lists these variables with placeholders:

| Variable | Used for |
| --- | --- |
| `DASHSCOPE_API_KEY` | DashScope Qwen model calls (`qwen3.7-max`) |
| `QWEN_INPUT_COST_PER_MILLION_CNY` | Current official input-token price used by the per-run budget |
| `QWEN_OUTPUT_COST_PER_MILLION_CNY` | Current official output-token price used by the per-run budget |
| `SCIENCE125_CROSSREF_MAILTO` | Crossref polite-contact metadata |
| `SCIENCE125_OPENALEX_MAILTO` | OpenAlex polite-contact metadata |
| `SCIENCE125_NCBI_API_KEY` | NCBI keyed quota for biomedical profiles |
| `SCIENCE125_NCBI_TOOL_EMAIL` | NCBI registered tool/contact identifier |
| `SCIENCE125_SEMANTIC_SCHOLAR_API_KEY` | Required chemistry-interface pilot source |
| `SCIENCE125_NASA_ADS_API_TOKEN` | Required high-energy astronomy pilot source |

For a temporary PowerShell session, set values explicitly before starting the backend
and remove them afterward:

```powershell
$env:DASHSCOPE_API_KEY = '<DashScope key>'
$env:QWEN_INPUT_COST_PER_MILLION_CNY = '<current official input price>'
$env:QWEN_OUTPUT_COST_PER_MILLION_CNY = '12'
$env:SCIENCE125_CROSSREF_MAILTO = '<registered contact email>'
$env:SCIENCE125_OPENALEX_MAILTO = '<registered contact email>'
$env:SCIENCE125_NCBI_API_KEY = '<NCBI key>'
$env:SCIENCE125_NCBI_TOOL_EMAIL = '<registered tool email>'
$env:SCIENCE125_SEMANTIC_SCHOLAR_API_KEY = '<Semantic Scholar key>'
$env:SCIENCE125_NASA_ADS_API_TOKEN = '<NASA ADS token>'

powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\dev.ps1

Remove-Item Env:DASHSCOPE_API_KEY,Env:QWEN_INPUT_COST_PER_MILLION_CNY,Env:QWEN_OUTPUT_COST_PER_MILLION_CNY,Env:SCIENCE125_CROSSREF_MAILTO,Env:SCIENCE125_OPENALEX_MAILTO,Env:SCIENCE125_NCBI_API_KEY,Env:SCIENCE125_NCBI_TOOL_EMAIL,Env:SCIENCE125_SEMANTIC_SCHOLAR_API_KEY,Env:SCIENCE125_NASA_ADS_API_TOKEN
```

`GET /api/science-125/questions/{questionId}/profile` reports the selected public
providers and whether required configuration is ready. Missing credentials block a
required profile rather than silently lowering its evidence standard. Only the three
audited pilot IDs can create a dedicated generation job; the legacy chemistry prompt
is never a fallback.

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
ignored `data/` directory. It does not accept invented, metadata-only, or
browser-assembled evidence: each pilot needs at least three human-reviewed
full-text records spanning at least two provider families.

Create an ignored controlled evidence file mapping every pilot ID to its reviewed
records. Keep the records' stable IDs, provider, provider family, title, abstract,
and `open_full_text`/`open_access` status so the runner can rebuild its evidence
snapshot. This minimal shape is illustrative only; replace it with reviewed records
from the official API search:

```json
{
  "S125-006": [{ "stableId": "doi:...", "provider": "crossref", "providerFamily": "doi_registry", "title": "...", "abstract": "...", "accessStatus": "open_full_text" }],
  "S125-043": [],
  "S125-054": []
}
```

Do not run the command until every array contains three qualifying records. The
complete booklet context index from the earlier section must also exist at
`data\\science125\\science125-context-v1.json`.

```powershell
$env:DASHSCOPE_API_KEY = '<session-only key>'
$env:QWEN_INPUT_COST_PER_MILLION_CNY = '<current official input price>'
$env:QWEN_OUTPUT_COST_PER_MILLION_CNY = '12'
python scripts/run_science_canary.py `
  --manifest benchmarks/science125/science125-v1.json `
  --evidence data/canary-reviewed-evidence.json `
  --output-dir data/canary `
  --summary-path data/canary-summary.json `
  --ledger-path data/canary-ledger.db `
  --require-cost-budget
Remove-Item Env:DASHSCOPE_API_KEY,Env:QWEN_INPUT_COST_PER_MILLION_CNY,Env:QWEN_OUTPUT_COST_PER_MILLION_CNY
```

`--max-total-tokens` defaults to 120,000 and each Qwen completion is capped at
8,192 completion tokens. To enforce a model-price budget as well, pass current
DashScope prices with `--input-cost-per-million-cny`,
`--output-cost-per-million-cny`, and a positive `--max-estimated-cost-cny`.
The runner rejects a cost ceiling when either price is unknown instead of
recording a misleading budget.

For GitHub, create a protected environment named `qwen-canary`, add its
`DASHSCOPE_API_KEY` secret, and define `QWEN_MAX_ESTIMATED_COST_CNY`,
`QWEN_INPUT_COST_PER_MILLION_CNY`, and
`QWEN_OUTPUT_COST_PER_MILLION_CNY` environment variables using the current
DashScope model price. Add these three additional **environment secrets** (not
variables):

| Secret | Value |
| --- | --- |
| `SCIENCE125_CANARY_CONTEXT_INDEX_GZIP_B64_PART1` | First half of the gzip-base64 encoded authorized `science125-context-v1.json` index |
| `SCIENCE125_CANARY_CONTEXT_INDEX_GZIP_B64_PART2` | Second half of that exact encoded index |
| `SCIENCE125_CANARY_REVIEWED_EVIDENCE_B64` | Base64 encoded controlled reviewed-evidence JSON for all three pilots |

The context index exceeds GitHub's single-secret limit after base64 encoding, so
the workflow uses two parts. On the machine that holds the authorized PDF and
reviewed evidence, generate an ignored helper file with the exact name/value pairs:

```powershell
$contextBytes = [IO.File]::ReadAllBytes('data\science125\science125-context-v1.json')
$buffer = [IO.MemoryStream]::new()
$gzip = [IO.Compression.GzipStream]::new($buffer, [IO.Compression.CompressionLevel]::Optimal, $true)
$gzip.Write($contextBytes, 0, $contextBytes.Length)
$gzip.Dispose()
$encodedContext = [Convert]::ToBase64String($buffer.ToArray())
$splitAt = [math]::Ceiling($encodedContext.Length / 2)
$encodedEvidence = [Convert]::ToBase64String([IO.File]::ReadAllBytes('data\canary-reviewed-evidence.json'))
if ($encodedEvidence.Length -gt 48000) { throw 'Reviewed evidence exceeds one GitHub secret; reduce it to reviewed metadata and abstracts.' }
@(
  "SCIENCE125_CANARY_CONTEXT_INDEX_GZIP_B64_PART1=$($encodedContext.Substring(0, $splitAt))"
  "SCIENCE125_CANARY_CONTEXT_INDEX_GZIP_B64_PART2=$($encodedContext.Substring($splitAt))"
  "SCIENCE125_CANARY_REVIEWED_EVIDENCE_B64=$encodedEvidence"
) | Set-Content -Encoding ascii 'data\canary-github-secret-values.txt'
```

Paste each value after the first `=` into the identically named GitHub environment
secret, then delete `data\canary-github-secret-values.txt` after configuration.
The manual **Qwen Canary** workflow materializes and validates these inputs before
it reads the DashScope key, so missing or corrupt inputs cannot spend model quota.
Its private output artifact is retained for 14 days. The normal PR workflow never
uses the key or spends model quota.

In development, configured provider keys remain on the backend and are never returned as plaintext; production uses process-environment credentials and ignores the plaintext store. Uploaded files and managed assets are stored under user-specific runtime directories, and server paths are never exposed by API responses. Model-backed document analysis rejects documents above `CHALK_MAX_ANALYSIS_CHARS` rather than silently truncating them. Hypothesis source context is explicitly balanced to 60,000 document characters plus up to 40,000 supplemental characters; any document truncation is returned as a Job warning.
