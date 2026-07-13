# Chalk Web Scientific Core

This directory is the Web-compatible scientific core bundled with Chalk Web.
The transitional bootstrap keeps established flat imports working while the
backend migrates toward package-qualified imports.

| Directory | Function |
|---|---|
| `core/` | Database, authentication, resource paths, and unified LLM client. |
| `literature/` | PDF parsing, document image extraction, RAG, evidence gating, and OA literature search. |
| `agents/` | Multi-agent AI Scientist pipeline, hypothesis iteration, HITL traces, and workflow export. |
| `multimodal/` | Scientific image/table analysis, data cleaning, association mining, and figure helpers. |
| `science/` | Chemistry/materials tools, PubChem/hazard helpers, RDKit/pymatgen/VASP/atomate2 validation. |
| `reports/` | Interactive HTML report rendering. |
| `prompts/` | LLM prompt templates used by extraction and scientific workflows. |

Desktop-only `app/`, `ui/`, the Qt hypothesis worker, and manual PDF conversion
script are deliberately excluded. Protected runtime data remains outside
`src/` under the standalone project's `data/` directory.
