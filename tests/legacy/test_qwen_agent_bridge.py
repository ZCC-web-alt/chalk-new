import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from llm_client import LLMConfig
from qwen_agent_bridge import (
    ChalkToolContext,
    build_function_list,
    build_qwen_llm_cfg,
    call_chalk_tool,
    format_tool_context,
    get_chalk_tool_specs,
    messages_to_text,
    parse_tool_params,
    qwen_agent_available,
)
from report_renderer import HTMLReportRenderer
from agent_framework import (
    AgentMessage,
    AgentRole,
    HypothesisAgent,
    HYPOTHESIS_LLM_TIMEOUT_SECONDS,
    HypothesisResult,
    LiteratureAgent,
    OutputAgent,
    ResultsVerificationAgent,
)
from agent_framework import HypothesisOrchestrator, LLMServiceError, _raise_if_llm_error
from llm_client import LLM_ERROR_PREFIX, _chat


def test_tool_specs_cover_chalk_capabilities():
    specs = get_chalk_tool_specs()
    names = {spec["name"] for spec in specs}

    assert "chalk_pubchem_lookup" in names
    assert "chalk_crossref_search" in names
    assert "chalk_rag_context" in names
    assert "chalk_database_evidence_query" in names
    assert "chalk_render_report" in names
    assert all(spec["parameters"] for spec in specs)


def test_parse_tool_params_accepts_json_string():
    assert parse_tool_params('{"name": "ethanol"}') == {"name": "ethanol"}
    assert parse_tool_params({"name": "water"}) == {"name": "water"}


def test_llm_cfg_uses_dashscope_qwen_model():
    cfg = build_qwen_llm_cfg(LLMConfig(api_key="test-key"), task="hypothesis")

    assert cfg["model"]
    assert cfg["model_type"] == "qwen_dashscope"
    assert cfg["api_key"] == "test-key"
    assert cfg["generate_cfg"]["fncall_prompt_type"] == "nous"


def test_render_report_tool_runs_without_qwen_agent():
    result = json.loads(
        call_chalk_tool(
            "chalk_render_report",
            {
                "hypothesis_json": json.dumps(
                    {
                        "paper_title": "Qwen Tool Report",
                        "confidence": 7,
                        "feasibility": "中",
                        "problem_statement": "验证工具渲染。",
                    },
                    ensure_ascii=False,
                ),
                "save": False,
            },
            ChalkToolContext(),
        )
    )

    assert result["ok"] is True
    assert result["data"]["html_length"] > 1000


def test_format_tool_context_includes_crossref_titles():
    text = format_tool_context(
        [
            {
                "tool": "chalk_crossref_search",
                "result": {
                    "ok": True,
                    "data": [
                        {
                            "authors": "A. Author",
                            "title": "Catalysis Paper",
                            "journal": "Journal",
                            "year": "2024",
                            "doi": "10.1/example",
                        }
                    ],
                },
            }
        ]
    )

    assert "Catalysis Paper" in text
    assert "10.1/example" in text


def test_messages_to_text_extracts_qwen_agent_content():
    text = messages_to_text(
        [
            {"role": "assistant", "content": "工具调用完成"},
            {"role": "assistant", "content": "最终证据摘要"},
        ]
    )

    assert "工具调用完成" in text
    assert "最终证据摘要" in text


def test_report_renders_qwen_agent_tool_section():
    result = HypothesisResult(
        raw_json={
            "paper_title": "Qwen Agent Section",
            "confidence": 8,
            "feasibility": "高",
            "problem_statement": "问题",
            "_qwen_agent_tool_context": {
                "mode": "local_tools",
                "tools": ["chalk_rag_context", "chalk_crossref_search"],
                "tool_calls": [
                    {
                        "tool": "chalk_crossref_search",
                        "result": {
                            "ok": True,
                            "data": [{"title": "Evidence Paper", "doi": "10.1/evidence"}],
                        },
                    }
                ],
            },
        }
    )

    html = HTMLReportRenderer().render(result, enhance=False)

    assert "Qwen-Agent 工具调用" in html
    assert "Evidence Paper" in html


def test_report_renders_qwen_agent_evidence_audit():
    result = HypothesisResult(
        raw_json={
            "paper_title": "Qwen Audit Section",
            "confidence": 8,
            "feasibility": "高",
            "problem_statement": "ORR claim requires *OOH evidence.",
            "_qwen_agent_tool_context": {
                "mode": "local_tools",
                "tools": ["chalk_database_evidence_query"],
                "qwen_agent_audit": {
                    "claims": [
                        {
                            "claim": "Co-N4@COF tunes *OOH adsorption for ORR",
                            "support_status": "supported",
                            "tool": "chalk_database_evidence_query",
                            "evidence_ids": ["CE-1", "CE-2"],
                            "notes": "Matched same active site adsorption energies.",
                        },
                        {
                            "claim": "Long-term stability is proven",
                            "support_status": "unsupported",
                            "tool": "chalk_database_evidence_query",
                            "evidence_ids": [],
                            "notes": "No stability record found.",
                        },
                    ]
                },
            },
        }
    )

    html = HTMLReportRenderer().render(result, enhance=False)

    assert "qwen-agent-audit" in html
    assert "Co-N4@COF tunes *OOH adsorption" in html
    assert "CE-1" in html
    assert "unsupported" in html


def test_qwen_timeout_is_not_reported_as_json_parse_error():
    import requests
    from unittest.mock import patch

    def raise_timeout(*args, **kwargs):
        raise requests.exceptions.ReadTimeout(
            "HTTPSConnectionPool(host='dashscope.aliyuncs.com', port=443): Read timed out."
        )

    with patch("llm_client.requests.post", raise_timeout):
        raw = _chat(
            "生成假设",
            LLMConfig(api_key="test-key"),
            task="hypothesis",
            timeout=1,
            retries=0,
        )
    parsed = HypothesisOrchestrator._parse_json_raw(raw)

    assert raw.startswith(LLM_ERROR_PREFIX)
    assert parsed["llm_error"] is True
    assert parsed["error_type"] == "timeout"
    assert "接口请求超时" in parsed["message"]
    assert "parse_error" not in parsed


def test_llm_error_marker_raises_clear_service_error():
    raw = (
        LLM_ERROR_PREFIX
        + '{"error_type":"timeout","model":"qwen3.7-max","message":"通义千问接口请求超时。","detail":"read timeout=1000"}'
    )

    try:
        _raise_if_llm_error(raw, stage="生成初始假设")
    except LLMServiceError as exc:
        text = str(exc)
        assert "生成初始假设失败" in text
        assert "qwen3.7-max" in text
        assert "read timeout=1000" in text
    else:
        raise AssertionError("LLMServiceError was not raised")


def test_hypothesis_pipeline_long_llm_calls_wait_up_to_1500_seconds():
    class RecordingMixin:
        def _call_llm(self, prompt, timeout=None):
            self.recorded_timeout = timeout
            return '{"ok": true}'

    class RecordingLiteratureAgent(RecordingMixin, LiteratureAgent):
        pass

    class RecordingHypothesisAgent(RecordingMixin, HypothesisAgent):
        pass

    class RecordingResultsAgent(RecordingMixin, ResultsVerificationAgent):
        pass

    class RecordingOutputAgent(RecordingMixin, OutputAgent):
        pass

    literature_agent = RecordingLiteratureAgent(LLMConfig(api_key="test-key"))
    literature_agent.process(
        AgentMessage(role=AgentRole.USER, content="paper text", metadata={})
    )
    assert literature_agent.recorded_timeout == HYPOTHESIS_LLM_TIMEOUT_SECONDS == 1500

    hypothesis_agent = RecordingHypothesisAgent(LLMConfig(api_key="test-key"))
    hypothesis_agent.process(
        AgentMessage(role=AgentRole.LITERATURE, content="{}", metadata={})
    )
    assert hypothesis_agent.recorded_timeout == HYPOTHESIS_LLM_TIMEOUT_SECONDS == 1500

    results_agent = RecordingResultsAgent(LLMConfig(api_key="test-key"))
    results_agent.process(
        AgentMessage(role=AgentRole.HYPOTHESIS, content="{}", metadata={})
    )
    assert results_agent.recorded_timeout == HYPOTHESIS_LLM_TIMEOUT_SECONDS

    output_agent = RecordingOutputAgent(LLMConfig(api_key="test-key"))
    output_agent.process(
        AgentMessage(role=AgentRole.HYPOTHESIS, content="{}", metadata={})
    )
    assert output_agent.recorded_timeout == HYPOTHESIS_LLM_TIMEOUT_SECONDS


if __name__ == "__main__":
    test_tool_specs_cover_chalk_capabilities()
    test_parse_tool_params_accepts_json_string()
    test_llm_cfg_uses_dashscope_qwen_model()
    test_render_report_tool_runs_without_qwen_agent()
    test_format_tool_context_includes_crossref_titles()
    test_messages_to_text_extracts_qwen_agent_content()
    test_report_renders_qwen_agent_tool_section()
    test_report_renders_qwen_agent_evidence_audit()
    test_qwen_timeout_is_not_reported_as_json_parse_error()
    test_llm_error_marker_raises_clear_service_error()
    test_hypothesis_pipeline_long_llm_calls_wait_up_to_1500_seconds()
    print(f"Qwen-Agent bridge tests passed (qwen-agent installed={qwen_agent_available()})")
