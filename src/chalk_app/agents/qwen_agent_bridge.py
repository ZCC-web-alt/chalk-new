# -*- coding: utf-8 -*-
"""
Qwen-Agent bridge for Chalk.

This module wraps Chalk's existing chemistry/literature/RAG/report capabilities
as Qwen-Agent-compatible tools while keeping the dependency optional. When
`qwen-agent` is installed, the same tool specs are registered with the official
Assistant runtime; without it, Chalk still exposes deterministic local tool
calls for tests and fallback workflows.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from llm_client import LLMConfig, MODEL_MAP, _ensure_config


CHALK_QWEN_SYSTEM_MESSAGE = (
    "你是 Chalk 的科学研究智能体。你可以调用 Chalk 工具检索真实文献、"
    "查询 PubChem、读取本地 RAG 片段、渲染交互式报告。所有结论必须区分"
    "工具返回的证据、模型推理和仍需人工核验的假设。"
)


@dataclass
class ChalkToolContext:
    """Runtime context shared by Chalk Qwen tools."""

    user_id: int = 0
    doc_id: Optional[int] = None
    api_key: str = ""
    domain: str = ""
    base_dir: str = ""
    report_dir: str = ""
    max_rag_chunks: int = 8
    min_rag_score: float = 0.25


@dataclass(frozen=True)
class ChalkToolSpec:
    """A small contract that can be converted to Qwen-Agent BaseTool classes."""

    name: str
    description: str
    parameters: List[Dict[str, Any]]


@dataclass
class ChalkToolResult:
    ok: bool
    tool: str
    data: Any = None
    error: str = ""
    platform_status: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(
            {
                "ok": self.ok,
                "tool": self.tool,
                "data": self.data,
                "error": self.error,
                "platform_status": self.platform_status,
                "warnings": self.warnings,
            },
            ensure_ascii=False,
        )


TOOL_SPECS = [
    ChalkToolSpec(
        name="chalk_pubchem_lookup",
        description="查询 PubChem 化合物基础信息，包括 CID、分子式、分子量、CAS、SMILES、InChIKey 与结构图链接。",
        parameters=[
            {
                "name": "name",
                "type": "string",
                "description": "化合物英文名、常用名或 CAS 号，例如 ethanol、sulfuric acid。",
                "required": True,
            }
        ],
    ),
    ChalkToolSpec(
        name="chalk_crossref_search",
        description="通过 Chalk 文献检索器查询真实论文元数据，默认优先 Crossref，可返回 DOI、作者、期刊、年份和摘要。",
        parameters=[
            {
                "name": "query",
                "type": "string",
                "description": "论文检索关键词或研究问题。",
                "required": True,
            },
            {
                "name": "max_results",
                "type": "integer",
                "description": "最多返回结果数，建议 3-8。",
                "required": False,
            },
            {
                "name": "platform",
                "type": "string",
                "description": "检索平台：crossref、semantic_scholar、all。",
                "required": False,
            },
        ],
    ),
    ChalkToolSpec(
        name="chalk_literature_search",
        description="Search compliant literature sources with platform diagnostics, including Crossref metadata, arXiv, DOAJ, PMC, and optional Semantic Scholar.",
        parameters=[
            {
                "name": "query",
                "type": "string",
                "description": "Search keywords, DOI, or research question.",
                "required": True,
            },
            {
                "name": "max_results",
                "type": "integer",
                "description": "Maximum results to return, typically 3-8.",
                "required": False,
            },
            {
                "name": "platform",
                "type": "string",
                "description": "Platform: crossref, arxiv, semantic_scholar, doaj, pmc, or all.",
                "required": False,
            },
        ],
    ),
    ChalkToolSpec(
        name="chalk_rag_context",
        description="从当前用户导入的文献片段中构建 RAG 上下文，用于回答研究问题或支撑假设生成。",
        parameters=[
            {
                "name": "question",
                "type": "string",
                "description": "需要从本地文献库检索的研究问题。",
                "required": True,
            },
            {
                "name": "doc_id",
                "type": "integer",
                "description": "可选：限定文档 ID；不传则检索当前上下文配置的文档。",
                "required": False,
            },
            {
                "name": "max_chunks",
                "type": "integer",
                "description": "最多返回片段数。",
                "required": False,
            },
        ],
    ),
    ChalkToolSpec(
        name="chalk_render_report",
        description="将结构化假设 JSON 渲染为 Chalk 交互式 HTML 报告，可用于比赛展示和导出。",
        parameters=[
            {
                "name": "hypothesis_json",
                "type": "string",
                "description": "结构化假设 JSON 字符串。",
                "required": True,
            },
            {
                "name": "save",
                "type": "boolean",
                "description": "是否保存为 HTML 文件。",
                "required": False,
            },
        ],
    ),
]

TOOL_SPECS.append(
    ChalkToolSpec(
        name="chalk_database_evidence_query",
        description="Query Chalk local evidence database for auditable LE/DE/CE evidence IDs, domain metrics, and same-site adsorbate energy sets.",
        parameters=[
            {
                "name": "query",
                "type": "string",
                "description": "Research question or claim to audit.",
                "required": True,
            },
            {
                "name": "domain",
                "type": "string",
                "description": "Optional domain such as electrocatalysis or battery.",
                "required": False,
            },
            {
                "name": "reaction_type",
                "type": "string",
                "description": "Optional reaction type such as ORR, OER, HER, CO2RR, or NRR.",
                "required": False,
            },
            {
                "name": "material",
                "type": "string",
                "description": "Optional target material or catalyst descriptor.",
                "required": False,
            },
            {
                "name": "adsorbates",
                "type": "array",
                "description": "Optional adsorbates such as *OOH, *O, *OH.",
                "required": False,
            },
            {
                "name": "max_each",
                "type": "integer",
                "description": "Maximum rows per evidence type.",
                "required": False,
            },
        ],
    )
)

_ACTIVE_CONTEXT = ChalkToolContext()
_TOOLS_REGISTERED = False


def get_chalk_tool_specs() -> List[Dict[str, Any]]:
    """Return serializable tool specs for UI, traces, and tests."""
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        }
        for spec in TOOL_SPECS
    ]


def qwen_agent_available() -> bool:
    try:
        import qwen_agent  # noqa: F401
        return True
    except Exception:
        return False


def build_qwen_llm_cfg(config: Optional[LLMConfig] = None, task: str = "hypothesis") -> Dict[str, Any]:
    cfg = _ensure_config(config)
    api_key = cfg.api_key or os.getenv("DASHSCOPE_API_KEY") or os.getenv("QWEN_API_KEY")
    model = cfg.model or MODEL_MAP.get(task, MODEL_MAP.get("qa", "qwen3.7-plus"))

    if cfg.base_url and "dashscope.aliyuncs.com" not in cfg.base_url:
        return {
            "model": model,
            "model_server": cfg.base_url.rstrip("/"),
            "api_key": api_key or "EMPTY",
            "generate_cfg": {
                "top_p": 0.8,
                "fncall_prompt_type": "nous",
            },
        }

    llm_cfg = {
        "model": model,
        "model_type": "qwen_dashscope",
        "generate_cfg": {
            "top_p": 0.8,
            "fncall_prompt_type": "nous",
        },
    }
    if api_key:
        llm_cfg["api_key"] = api_key
    return llm_cfg


def parse_tool_params(params: Any) -> Dict[str, Any]:
    if isinstance(params, dict):
        return params
    if not params:
        return {}
    if isinstance(params, str):
        try:
            return json.loads(params)
        except json.JSONDecodeError:
            try:
                import json5
                return json5.loads(params)
            except Exception:
                return {"_raw": params}
    return {"value": params}


def call_chalk_tool(name: str, params: Any, context: Optional[ChalkToolContext] = None) -> str:
    ctx = context or ChalkToolContext()
    args = parse_tool_params(params)
    try:
        if name == "chalk_pubchem_lookup":
            return _tool_pubchem(args).to_json()
        if name in ("chalk_crossref_search", "chalk_literature_search"):
            return _tool_literature_search(args, ctx, name).to_json()
        if name == "chalk_rag_context":
            return _tool_rag_context(args, ctx).to_json()
        if name == "chalk_database_evidence_query":
            return _tool_database_evidence_query(args, ctx).to_json()
        if name == "chalk_render_report":
            return _tool_render_report(args, ctx).to_json()
        return ChalkToolResult(False, name, error=f"未知 Chalk Qwen 工具: {name}").to_json()
    except Exception as exc:
        return ChalkToolResult(False, name, error=str(exc)).to_json()


def _tool_pubchem(args: Dict[str, Any]) -> ChalkToolResult:
    name = str(args.get("name") or args.get("query") or "").strip()
    if not name:
        return ChalkToolResult(False, "chalk_pubchem_lookup", error="缺少参数 name")

    from chem_structure import query_chem_info

    info = query_chem_info(name)
    if info is None:
        return ChalkToolResult(False, "chalk_pubchem_lookup", error=f"PubChem 未找到: {name}")
    return ChalkToolResult(
        True,
        "chalk_pubchem_lookup",
        data={
            "name": info.name,
            "cid": info.cid,
            "iupac_name": info.iupac_name,
            "molecular_formula": info.molecular_formula,
            "molecular_weight": info.molecular_weight,
            "cas": info.cas,
            "canonical_smiles": info.canonical_smiles,
            "inchi_key": info.inchikey,
            "synonyms": info.synonyms[:10],
            "pubchem_url": info.pubchem_url,
            "image_url": info.image_url,
        },
    )


def _split_keywords(query: str) -> List[str]:
    terms = []
    for raw in query.replace("，", ",").replace("；", ",").split(","):
        term = raw.strip()
        if term:
            terms.append(term)
    if terms:
        return terms[:6]
    words = [w.strip() for w in query.split() if w.strip()]
    if len(words) <= 3:
        return [query.strip()]
    return [" ".join(words[i : i + 3]) for i in range(0, min(len(words), 9), 3)]


def _tool_literature_search(
    args: Dict[str, Any],
    ctx: ChalkToolContext,
    tool_name: str = "chalk_crossref_search",
) -> ChalkToolResult:
    query_text = str(args.get("query") or args.get("keywords") or "").strip()
    if not query_text:
        return ChalkToolResult(False, tool_name, error="缺少参数 query")

    default_platform = "all" if tool_name == "chalk_literature_search" else "crossref"
    platform = str(args.get("platform") or default_platform).strip().lower()
    max_results = int(args.get("max_results") or 5)
    max_results = max(1, min(max_results, 10))

    from literature_search import LiteratureSearchEngine, SearchQuery

    has_semantic_key = bool(os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip())
    if platform in ("all", "literature", "oa"):
        platforms = {
            "arxiv": True,
            "crossref": True,
            "semantic_scholar": has_semantic_key,
            "doaj": True,
            "pmc": True,
        }
    else:
        platforms = {
            "arxiv": platform == "arxiv",
            "crossref": platform == "crossref",
            "semantic_scholar": platform in ("semantic_scholar", "semantic"),
            "doaj": platform == "doaj",
            "pmc": platform == "pmc",
        }
    engine = LiteratureSearchEngine(platforms=platforms)
    diagnostics = engine.search_with_diagnostics(
        SearchQuery(
            keywords=_split_keywords(query_text),
            domain=ctx.domain,
            max_results=max_results,
            year_from="2015",
        )
    )
    return ChalkToolResult(
        True,
        tool_name,
        data=[r.to_reference_dict() for r in diagnostics.results[:max_results]],
        platform_status=diagnostics.platform_status,
        warnings=diagnostics.warnings,
    )


def _tool_rag_context(args: Dict[str, Any], ctx: ChalkToolContext) -> ChalkToolResult:
    question = str(args.get("question") or args.get("query") or "").strip()
    if not question:
        return ChalkToolResult(False, "chalk_rag_context", error="缺少参数 question")
    if not ctx.user_id:
        return ChalkToolResult(False, "chalk_rag_context", error="缺少 user_id，无法检索用户文献库")

    doc_id = args.get("doc_id", ctx.doc_id)
    if doc_id in ("", 0, "0"):
        doc_id = None
    max_chunks = int(args.get("max_chunks") or ctx.max_rag_chunks or 8)
    max_chunks = max(1, min(max_chunks, 30))

    from db import get_session
    from rag import build_hypothesis_context

    with get_session() as session:
        context_text = build_hypothesis_context(
            session=session,
            user_id=ctx.user_id,
            research_question=question,
            doc_id=int(doc_id) if doc_id is not None else None,
            max_chunks=max_chunks,
            min_score=ctx.min_rag_score,
            api_key=ctx.api_key or None,
        )
    return ChalkToolResult(
        True,
        "chalk_rag_context",
        data={
            "question": question,
            "context": context_text,
            "doc_id": doc_id,
            "max_chunks": max_chunks,
        },
    )


def _tool_database_evidence_query(args: Dict[str, Any], ctx: ChalkToolContext) -> ChalkToolResult:
    query_text = str(args.get("query") or args.get("question") or "").strip()
    if not query_text:
        return ChalkToolResult(False, "chalk_database_evidence_query", error="missing query")
    if not ctx.user_id:
        return ChalkToolResult(False, "chalk_database_evidence_query", error="missing user_id for local evidence database")

    adsorbates = args.get("adsorbates")
    if isinstance(adsorbates, str):
        adsorbates = [item.strip() for item in adsorbates.replace("，", ",").split(",") if item.strip()]
    elif not isinstance(adsorbates, list):
        adsorbates = []
    max_each = int(args.get("max_each") or 6)
    max_each = max(1, min(max_each, 20))

    from db import get_session
    from evidence_database import evidence_search_bundle_to_dict, query_evidence

    with get_session() as session:
        bundle = query_evidence(
            session,
            user_id=ctx.user_id,
            domain=str(args.get("domain") or ctx.domain or ""),
            query_text=query_text,
            reaction_type=str(args.get("reaction_type") or ""),
            material=str(args.get("material") or ""),
            adsorbates=adsorbates,
            max_each=max_each,
        )
        data = evidence_search_bundle_to_dict(bundle)
    return ChalkToolResult(
        True,
        "chalk_database_evidence_query",
        data={
            "query": query_text,
            "domain": str(args.get("domain") or ctx.domain or ""),
            "reaction_type": str(args.get("reaction_type") or ""),
            "material": str(args.get("material") or ""),
            "adsorbates": adsorbates,
            "evidence": data,
            "evidence_ids": _extract_evidence_ids_from_data(data),
        },
        warnings=data.get("warnings", []) if isinstance(data, dict) else [],
    )


def _coerce_hypothesis_json(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return json.loads(raw)
    raise ValueError("hypothesis_json 必须是 JSON 字符串或对象")


def _tool_render_report(args: Dict[str, Any], ctx: ChalkToolContext) -> ChalkToolResult:
    raw = args.get("hypothesis_json") or args.get("data") or args.get("hypothesis")
    data = _coerce_hypothesis_json(raw)

    from agent_framework import HypothesisResult
    from report_renderer import HTMLReportRenderer

    result = HypothesisResult(raw_json=data, final_output=json.dumps(data, ensure_ascii=False))
    renderer = HTMLReportRenderer()
    html = renderer.render(result, enhance=False)

    save = bool(args.get("save", False))
    path = ""
    if save:
        report_dir = ctx.report_dir or os.path.join(ctx.base_dir or os.getcwd(), "data", "reports")
        os.makedirs(report_dir, exist_ok=True)
        title = str(data.get("paper_title") or "qwen_agent_report")
        safe_title = "".join(c for c in title[:30] if c.isalnum() or c in "._- ") or "qwen_agent_report"
        path = os.path.join(report_dir, f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_title}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)

    return ChalkToolResult(
        True,
        "chalk_render_report",
        data={
            "html": html[:2000] if not save else "",
            "saved_path": path,
            "html_length": len(html),
        },
    )


def register_chalk_qwen_tools(context: Optional[ChalkToolContext] = None) -> List[str]:
    """
    Register Chalk tools with the official Qwen-Agent runtime.

    Returns the function names that can be included in Assistant(function_list=...).
    Raises ImportError if qwen-agent is not installed.
    """
    from qwen_agent.tools.base import BaseTool, register_tool

    global _ACTIVE_CONTEXT, _TOOLS_REGISTERED
    _ACTIVE_CONTEXT = context or ChalkToolContext()
    if _TOOLS_REGISTERED:
        return [spec.name for spec in TOOL_SPECS]

    for spec in TOOL_SPECS:
        attrs = {
            "description": spec.description,
            "parameters": spec.parameters,
            "call": _make_qwen_tool_call(spec.name),
        }
        tool_cls = type(
            "".join(part.capitalize() for part in spec.name.split("_")) + "Tool",
            (BaseTool,),
            attrs,
        )
        register_tool(spec.name)(tool_cls)
    _TOOLS_REGISTERED = True
    return [spec.name for spec in TOOL_SPECS]


def _make_qwen_tool_call(tool_name: str):
    def _call(self, params: str, **kwargs) -> str:
        return call_chalk_tool(tool_name, params, _ACTIVE_CONTEXT)

    return _call


def build_function_list(
    context: Optional[ChalkToolContext] = None,
    *,
    include_code_interpreter: bool = True,
    mcp_servers: Optional[Dict[str, Any]] = None,
) -> List[Any]:
    """Build a Qwen-Agent function_list with Chalk tools, Code Interpreter, and optional MCP."""
    tools: List[Any] = register_chalk_qwen_tools(context)
    if include_code_interpreter:
        tools.append("code_interpreter")
    if mcp_servers:
        tools.append({"mcpServers": mcp_servers})
    return tools


def run_qwen_agent(
    prompt: str,
    *,
    config: Optional[LLMConfig] = None,
    context: Optional[ChalkToolContext] = None,
    include_code_interpreter: bool = False,
    mcp_servers: Optional[Dict[str, Any]] = None,
    files: Optional[List[str]] = None,
    system_message: str = CHALK_QWEN_SYSTEM_MESSAGE,
) -> Dict[str, Any]:
    """
    Run the official Qwen-Agent Assistant once and return its final messages.

    This function is intentionally optional: callers should check
    qwen_agent_available() or catch ImportError.
    """
    from qwen_agent.agents import Assistant

    llm_cfg = build_qwen_llm_cfg(config, task="hypothesis")
    function_list = build_function_list(
        context,
        include_code_interpreter=include_code_interpreter,
        mcp_servers=mcp_servers,
    )
    bot = Assistant(
        llm=llm_cfg,
        system_message=system_message,
        function_list=function_list,
        files=files or [],
    )
    messages = [{"role": "user", "content": prompt}]
    final_response: List[Dict[str, Any]] = []
    for response in bot.run(messages=messages):
        final_response = response
    return {
        "ok": True,
        "messages": final_response,
        "tools": [
            item if isinstance(item, str) else "mcpServers"
            for item in function_list
        ],
    }


def build_chalk_qwen_tool_context(
    research_question: str,
    *,
    config: Optional[LLMConfig] = None,
    context: Optional[ChalkToolContext] = None,
    use_official_agent: bool = True,
) -> Dict[str, Any]:
    """
    Build concise evidence context for Chalk's hypothesis pipeline.

    If Qwen-Agent is installed, this can use the official Assistant runtime.
    Otherwise it falls back to deterministic local tool calls so the pipeline
    remains usable and testable.
    """
    ctx = context or ChalkToolContext()
    prompt = (
        "请围绕以下研究问题调用 Chalk 工具，返回可用于科学假设生成的证据摘要：\n"
        f"{research_question}\n\n"
        "必须优先查本地 RAG 和真实文献，不要编造引用。"
    )
    if use_official_agent and qwen_agent_available():
        try:
            agent_result = run_qwen_agent(
                prompt,
                config=config,
                context=ctx,
                include_code_interpreter=False,
            )
            return {
                "mode": "qwen_agent",
                **agent_result,
                "summary": messages_to_text(agent_result.get("messages", [])),
            }
        except Exception as exc:
            fallback = _build_local_tool_context(research_question, ctx)
            fallback["agent_error"] = str(exc)
            return fallback
    return _build_local_tool_context(research_question, ctx)


def messages_to_text(messages: Any, max_chars: int = 6000) -> str:
    """Extract readable text from Qwen-Agent message structures."""
    if not messages:
        return ""
    if isinstance(messages, str):
        return messages[:max_chars]
    if isinstance(messages, dict):
        content = messages.get("content", "")
        if isinstance(content, str):
            return content[:max_chars]
        return json.dumps(content, ensure_ascii=False)[:max_chars]
    if isinstance(messages, list):
        parts = []
        for msg in messages:
            if isinstance(msg, dict):
                content = msg.get("content", "")
                role = msg.get("role", "")
                if content:
                    parts.append(f"[{role}] {content}" if role else str(content))
            elif msg:
                parts.append(str(msg))
        text = "\n".join(parts).strip()
        return text[:max_chars]
    return str(messages)[:max_chars]


def _build_local_tool_context(research_question: str, ctx: ChalkToolContext) -> Dict[str, Any]:
    calls = []
    if ctx.user_id:
        calls.append(
            {
                "tool": "chalk_rag_context",
                "result": json.loads(
                    call_chalk_tool(
                        "chalk_rag_context",
                        {
                            "question": research_question,
                            "doc_id": ctx.doc_id,
                            "max_chunks": ctx.max_rag_chunks,
                        },
                        ctx,
                    )
                ),
            }
        )
        calls.append(
            {
                "tool": "chalk_database_evidence_query",
                "result": json.loads(
                    call_chalk_tool(
                        "chalk_database_evidence_query",
                        {
                            "query": research_question,
                            "domain": ctx.domain,
                            "max_each": 6,
                        },
                        ctx,
                    )
                ),
            }
        )
    calls.append(
        {
            "tool": "chalk_crossref_search",
            "result": json.loads(
                call_chalk_tool(
                    "chalk_crossref_search",
                    {
                        "query": research_question,
                        "max_results": 3,
                        "platform": "crossref",
                    },
                    ctx,
                )
            ),
        }
    )
    return {
        "mode": "local_tools",
        "ok": True,
        "tools": [spec.name for spec in TOOL_SPECS],
        "tool_calls": calls,
        "summary": format_tool_context(calls),
        "qwen_agent_audit": build_qwen_agent_audit(calls, research_question),
    }


def format_tool_context(tool_calls: Iterable[Dict[str, Any]], max_chars: int = 6000) -> str:
    parts = []
    for call in tool_calls:
        tool = call.get("tool", "")
        result = call.get("result", {})
        if not isinstance(result, dict):
            continue
        if not result.get("ok"):
            parts.append(f"[{tool}] 工具失败: {result.get('error', '')}")
            continue
        data = result.get("data")
        if tool == "chalk_rag_context" and isinstance(data, dict):
            context = str(data.get("context", "")).strip()
            if context:
                parts.append(f"[Chalk RAG]\n{context}")
        elif tool in ("chalk_crossref_search", "chalk_literature_search") and isinstance(data, list):
            lines = []
            for i, ref in enumerate(data[:5], 1):
                title = ref.get("title", "")
                authors = ref.get("authors", "")
                journal = ref.get("journal", "")
                year = ref.get("year", "")
                doi = ref.get("doi", "")
                platform = ref.get("source_platform", "")
                access_status = ref.get("access_status", "")
                fulltext_flag = "needs_fulltext" if ref.get("needs_fulltext") else access_status
                lines.append(
                    f"{i}. {authors}, {title}, {journal} ({year}), DOI: {doi}"
                    f" [{platform}; {fulltext_flag}]"
                )
            warnings = result.get("warnings") if isinstance(result.get("warnings"), list) else []
            if warnings:
                lines.append("Warnings: " + "; ".join(str(w) for w in warnings[:3]))
            platform_status = result.get("platform_status")
            if isinstance(platform_status, dict) and platform_status:
                status_parts = []
                for platform, info in platform_status.items():
                    if isinstance(info, dict):
                        status_parts.append(
                            f"{platform}={info.get('status', '')}"
                            f"({info.get('count', info.get('added_count', 0))})"
                        )
                if status_parts:
                    lines.append("Platform status: " + "; ".join(status_parts[:8]))
            if lines:
                label = "开放文献候选" if tool == "chalk_literature_search" else "Crossref 文献候选"
                parts.append(f"[{label}]\n" + "\n".join(lines))
        elif tool == "chalk_database_evidence_query" and isinstance(data, dict):
            evidence = data.get("evidence", {}) if isinstance(data.get("evidence"), dict) else {}
            ids = data.get("evidence_ids", []) if isinstance(data.get("evidence_ids"), list) else []
            summary = evidence.get("summary", {}) if isinstance(evidence.get("summary"), dict) else {}
            lines = [
                f"Query: {data.get('query', '')}",
                "Evidence IDs: " + (", ".join(str(eid) for eid in ids[:16]) if ids else "none"),
                (
                    "Counts: "
                    f"LE={summary.get('literature_evidence_count', 0)}, "
                    f"DE={summary.get('domain_evidence_count', 0)}, "
                    f"CE={summary.get('computational_catalysis_evidence_count', 0)}, "
                    f"sets={summary.get('adsorbate_energy_set_count', 0)}"
                ),
            ]
            sets = evidence.get("adsorbate_energy_sets", [])
            if isinstance(sets, list):
                for aset in sets[:3]:
                    if not isinstance(aset, dict):
                        continue
                    coverage = aset.get("coverage", {}) if isinstance(aset.get("coverage"), dict) else {}
                    lines.append(
                        "Energy set: "
                        + ", ".join(
                            str(x)
                            for x in [
                                aset.get("material_system", ""),
                                aset.get("surface_facet", ""),
                                aset.get("active_site", ""),
                                "complete" if coverage.get("complete") else "missing=" + ",".join(coverage.get("missing_adsorbates", [])),
                            ]
                            if x
                        )
                    )
            parts.append("[Chalk database evidence]\n" + "\n".join(lines))
        else:
            parts.append(f"[{tool}]\n{json.dumps(data, ensure_ascii=False)[:1200]}")

    text = "\n\n".join(parts).strip()
    if len(text) > max_chars:
        return text[: max_chars - 1] + "…"
    return text


def _extract_evidence_ids_from_data(data: Any) -> List[str]:
    ids: List[str] = []

    def add(evidence_id: Any) -> None:
        value = str(evidence_id or "").strip()
        if value and value not in ids:
            ids.append(value)

    if not isinstance(data, dict):
        return ids
    for key, prefix in (
        ("literature_evidence", "LE"),
        ("domain_evidence", "DE"),
        ("computational_catalysis_evidence", "CE"),
    ):
        items = data.get(key, [])
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and item.get("id") not in (None, ""):
                add(f"{prefix}-{item.get('id')}")
    sets = data.get("adsorbate_energy_sets", [])
    if isinstance(sets, list):
        for item in sets:
            if not isinstance(item, dict):
                continue
            evidence_ids = item.get("evidence_ids", [])
            if isinstance(evidence_ids, list):
                for evidence_id in evidence_ids:
                    add(evidence_id)
    nested = data.get("evidence")
    if isinstance(nested, dict):
        for evidence_id in _extract_evidence_ids_from_data(nested):
            add(evidence_id)
    direct = data.get("evidence_ids")
    if isinstance(direct, list):
        for evidence_id in direct:
            add(evidence_id)
    return ids


def build_qwen_agent_audit(tool_calls: Iterable[Dict[str, Any]], research_question: str = "") -> Dict[str, Any]:
    claims = []
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        tool = call.get("tool", "")
        result = call.get("result", {}) if isinstance(call.get("result"), dict) else {}
        data = result.get("data") if isinstance(result, dict) else {}
        evidence_ids = _extract_evidence_ids_from_data(data)
        status = "supported" if result.get("ok") and evidence_ids else "unsupported"
        if not result.get("ok"):
            note = str(result.get("error", "tool failed"))
        elif not evidence_ids:
            note = "No auditable LE/DE/CE evidence IDs returned."
        else:
            note = f"{len(evidence_ids)} evidence IDs returned."
        claims.append(
            {
                "claim": research_question or "Tool evidence audit",
                "support_status": status,
                "tool": tool,
                "evidence_ids": evidence_ids[:20],
                "notes": note,
            }
        )
    return {"role": "evidence_audit", "claims": claims}
