# Legacy Test Migration Audit

Audit date: 2026-07-13

## Scope

The desktop repository is a read-only behavior reference. M0 migrates every
test that exercises the vendored, non-Qt Web core. Desktop UI/login tests and
the explicitly deferred heavyweight Docling and atomate2 integrations remain
outside the Web CI boundary.

The migrated suite runs with:

```bash
python -m pytest tests/legacy -q
```

Current result: 88 tests. This includes 86 applicable source tests plus two
additional NRR normalization regressions added during M0.

## Source File Disposition

| Source test | Source tests | Disposition | Web coverage |
| --- | ---: | --- | --- |
| `test_atomate2_dryrun.py` | 3 | Excluded | Heavy atomate2 integration explicitly deferred by the M0 plan. Web modeling remains dry-run and is covered by backend job/artifact tests. |
| `test_docling_and_evidence_rag.py` | 5 | Excluded | Heavy Docling integration explicitly deferred; the file also imports desktop worker/UI surfaces. |
| `test_document_image_extractor.py` | 3 | Migrated | `tests/legacy/test_document_image_extractor.py` |
| `test_domain_hypothesis_prompts.py` | 9 | Migrated | `tests/legacy/test_domain_hypothesis_prompts.py` |
| `test_evidence_database.py` | 17 | Migrated | `tests/legacy/test_evidence_database.py` |
| `test_evidence_database_ui.py` | 5 | Excluded | PySide6 `MainWindow` behavior. Web evidence workflows are covered by backend API and Playwright tests. |
| `test_evidence_gate.py` | 1 | Migrated | `tests/legacy/test_evidence_gate.py` |
| `test_evidence_workbench.py` | 5 | Migrated | `tests/legacy/test_evidence_workbench.py` |
| `test_hitl_trace.py` | 6 | Partially migrated: 4 | Diff, feedback, Agent Trace, and report rendering are in `tests/legacy/test_hitl_trace.py`; two `HumanFeedbackDialog` tests are Qt-only. |
| `test_hypothesis_multimodal_multidoc.py` | 3 | Partially migrated: 1 | Structured evidence query formatting is retained; two `chalk_app.app.main` desktop batch/preview tests are excluded. |
| `test_hypothesis_progress.py` | 4 | Migrated | `tests/legacy/test_hypothesis_progress.py` |
| `test_hypothesis_tree_search.py` | 3 | Migrated | `tests/legacy/test_hypothesis_tree_search.py` |
| `test_hypothesis_workflow_export.py` | 4 | Migrated | `tests/legacy/test_hypothesis_workflow_export.py` |
| `test_import_compatibility.py` | 2 | Partially migrated: 1 | Legacy flat multimodal exports are retained; the desktop `chalk_app.app.main` import is excluded. |
| `test_literature_search_resilience.py` | 9 | Migrated | `tests/legacy/test_literature_search_resilience.py` |
| `test_login_flow.py` | 0 | Excluded | Optional desktop database write smoke. Web registration/login/session/logout use the real full-stack Playwright test. |
| `test_model_routing.py` | 2 | Migrated | `tests/legacy/test_model_routing.py` |
| `test_multi_document_context.py` | 3 | Partially migrated: 2 | Pure context behavior is retained; the PySide6 `QThread` hypothesis worker test is excluded. |
| `test_multimodal_evidence.py` | 2 | Migrated | `tests/legacy/test_multimodal_evidence.py` |
| `test_qwen_agent_bridge.py` | 11 | Migrated | `tests/legacy/test_qwen_agent_bridge.py` |
| `test_scientific_toolkit.py` | 7 | Migrated | `tests/legacy/test_scientific_toolkit.py` |
| `test_scientific_validation.py` | 1 | Migrated | `tests/legacy/test_scientific_validation.py` |

## M0-Specific Regressions

- `test_evidence_database_nrr.py` locks the canonical NRR intermediate set and
  star-position aliases.
- Migrating the full evidence database suite exposed missing HOR facet
  normalization. The Web core now maps HOR to `*H` consistently in query,
  manifest, and material-token normalization.

## Adaptation Rules

- Production source was not copied from the desktop repository.
- Tests use the vendored `chalk_app` package or the legacy bootstrap in
  `tests/legacy/conftest.py`.
- Repository-root calculation changed only where the standalone Web layout
  required it.
- No test makes a real model or literature-network call.
- Qt windows, dialogs, workers, and desktop database writes are not imported
  by the Web legacy CI job.
