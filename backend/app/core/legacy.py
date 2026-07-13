from __future__ import annotations

import importlib
import sys
from functools import lru_cache
from pathlib import Path

from app.core.config import get_settings


def bootstrap_legacy() -> Path:
    """Expose the vendored scientific core and its transitional flat imports."""
    root = get_settings().legacy_root.resolve()
    src = root / "src"
    for path in (root, src):
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)
    importlib.import_module("chalk_app.bootstrap")
    return root


@lru_cache(maxsize=1)
def db():
    bootstrap_legacy()
    module = importlib.import_module("db")
    return module


@lru_cache(maxsize=1)
def auth():
    bootstrap_legacy()
    return importlib.import_module("auth")


@lru_cache(maxsize=1)
def llm_client():
    bootstrap_legacy()
    return importlib.import_module("llm_client")


@lru_cache(maxsize=1)
def rag():
    bootstrap_legacy()
    return importlib.import_module("rag")


@lru_cache(maxsize=1)
def pdf_utils():
    bootstrap_legacy()
    return importlib.import_module("pdf_utils")


@lru_cache(maxsize=1)
def literature_search():
    bootstrap_legacy()
    return importlib.import_module("literature_search")


@lru_cache(maxsize=1)
def evidence_database():
    bootstrap_legacy()
    return importlib.import_module("evidence_database")


@lru_cache(maxsize=1)
def agent_framework():
    bootstrap_legacy()
    return importlib.import_module("agent_framework")


@lru_cache(maxsize=1)
def document_image_extractor():
    bootstrap_legacy()
    return importlib.import_module("document_image_extractor")


@lru_cache(maxsize=1)
def chem_structure():
    bootstrap_legacy()
    return importlib.import_module("chem_structure")


@lru_cache(maxsize=1)
def hazard_db():
    bootstrap_legacy()
    return importlib.import_module("hazard_db")


@lru_cache(maxsize=1)
def scientific_evidence_rag():
    bootstrap_legacy()
    return importlib.import_module("scientific_evidence_rag")


@lru_cache(maxsize=1)
def report_renderer():
    bootstrap_legacy()
    return importlib.import_module("report_renderer")


@lru_cache(maxsize=1)
def hypothesis_workflow_exporter():
    bootstrap_legacy()
    return importlib.import_module("hypothesis_workflow_exporter")


@lru_cache(maxsize=1)
def hitl_trace():
    bootstrap_legacy()
    return importlib.import_module("hitl_trace")


@lru_cache(maxsize=1)
def multimodal():
    bootstrap_legacy()
    return importlib.import_module("multimodal")


@lru_cache(maxsize=1)
def multimodal_pipeline():
    bootstrap_legacy()
    return importlib.import_module("multimodal_pipeline")


@lru_cache(maxsize=1)
def data_cleaner():
    bootstrap_legacy()
    return importlib.import_module("data_cleaner")


@lru_cache(maxsize=1)
def data_miner():
    bootstrap_legacy()
    return importlib.import_module("data_miner")


@lru_cache(maxsize=1)
def vasp_defaults():
    bootstrap_legacy()
    return importlib.import_module("vasp_defaults")


@lru_cache(maxsize=1)
def scientific_toolkit():
    bootstrap_legacy()
    return importlib.import_module("scientific_toolkit")
