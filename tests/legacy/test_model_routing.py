import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_lightweight_and_balanced_tasks_use_qwen37_plus():
    from llm_client import DEFAULT_MODEL, MODEL_MAP

    qwen37_plus_tasks = {
        "summarize",
        "extract_names",
        "find_image_pages",
        "qa",
        "lab_suggest",
    }

    assert DEFAULT_MODEL == "qwen3.7-plus"
    for task in qwen37_plus_tasks:
        assert MODEL_MAP[task] == "qwen3.7-plus"

    assert "qwen3.6-flash" not in MODEL_MAP.values()
    assert "qwen3.6-plus" not in MODEL_MAP.values()


def test_qwen_agent_unknown_task_falls_back_to_qwen37_plus():
    from llm_client import LLMConfig
    from qwen_agent_bridge import build_qwen_llm_cfg

    cfg = build_qwen_llm_cfg(LLMConfig(api_key="test-key"), task="unknown_task")

    assert cfg["model"] == "qwen3.7-plus"


if __name__ == "__main__":
    test_lightweight_and_balanced_tasks_use_qwen37_plus()
    test_qwen_agent_unknown_task_falls_back_to_qwen37_plus()
    print("model routing tests passed")
