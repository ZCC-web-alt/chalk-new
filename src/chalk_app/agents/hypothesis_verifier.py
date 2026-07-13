"""
hypothesis_verifier.py — 假设结果自动验证模块

对 AI 生成的科学假设进行多维度真实数据验证，确保结果不是凭空捏造。
验证维度：
  1. 化学物质存在性 — 通过 PubChem API 验证假设中提及的化学品是否真实存在
  2. 参考文献真实性 — 通过 CrossRef API 验证 DOI 是否有效，论文是否存在
  3. 数值合理性检查 — 基于已知物理化学常数范围，检查预测数值是否合理
  4. LLM 综合分析 — 结合以上验证数据，由 AI 生成综合验证报告

比赛要求对应：
  "实验结果（Results）：通过公式推导或实际执行，在一定范围内验证该实验可行性"
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable

import requests

from llm_client import _chat, LLMConfig, _ensure_config, _detect_lang


# ─────────────────────────────────────────────────────────────
# 数据类
# ─────────────────────────────────────────────────────────────

@dataclass
class ChemicalVerification:
    """单个化学物质的验证结果"""
    name: str
    exists: bool = False
    cid: Optional[int] = None
    molecular_formula: str = ""
    molecular_weight: str = ""
    iupac_name: str = ""
    pubchem_url: str = ""
    error: str = ""  # 查询失败的错误信息


@dataclass
class ReferenceVerification:
    """单篇参考文献的验证结果"""
    index: int
    authors: str = ""
    title: str = ""
    doi: str = ""
    year: int = 0
    verified: bool = False
    status: str = ""  # "DOI 有效" / "DOI 无法访问" / "无 DOI" / "论文未找到"
    crossref_title: str = ""  # CrossRef 返回的实际标题
    error: str = ""
    # ── 深度验证字段 (P1: ReferenceDeepVerifier) ──
    abstract: str = ""                  # Semantic Scholar 返回的摘要
    key_findings: str = ""             # LLM 从摘要提取的关键结论
    consistency_score: float = 0.0     # 引用上下文与实际论文结论的一致性 (0-1)
    consistency_detail: str = ""       # 一致性评价详情
    deep_verified: bool = False        # 是否完成深度验证
    # ── 保真度标签 (P0: 引用准确性分级) ──
    fidelity_label: str = ""           # 忠实/轻微偏差/过度解读/严重歪曲/无法判定
    distortion_detail: str = ""       # 偏差的具体说明（如哪句话被歪曲、如何过度解读）
    search_method: str = ""            # "DOI" / "title" / "none" — 深度验证的查找方式


@dataclass
class NumericalCheck:
    """数值合理性检查结果"""
    claim: str = ""           # 原始数值声明
    quantity: str = ""        # 物理量名称（如 "activation energy"）
    value: float = 0.0        # 提取的数值
    unit: str = ""            # 单位
    is_reasonable: bool = True
    known_range: str = ""     # 已知的合理范围
    explanation: str = ""     # 说明


@dataclass
class VerificationReport:
    """完整的验证报告"""
    hypothesis_title: str = ""
    total_checks: int = 0
    passed_checks: int = 0
    failed_checks: int = 0
    warning_checks: int = 0

    chemicals: List[ChemicalVerification] = field(default_factory=list)
    references: List[ReferenceVerification] = field(default_factory=list)
    numerical_checks: List[NumericalCheck] = field(default_factory=list)

    llm_analysis: str = ""    # LLM 综合分析结果
    overall_verdict: str = "" # 总体评价
    credibility_score: int = 0  # 可信度评分 1-10

    verified_at: str = ""

    def to_json(self) -> dict:
        return {
            "hypothesis_title": self.hypothesis_title,
            "total_checks": self.total_checks,
            "passed_checks": self.passed_checks,
            "failed_checks": self.failed_checks,
            "warning_checks": self.warning_checks,
            "credibility_score": self.credibility_score,
            "chemicals": [
                {
                    "name": c.name, "exists": c.exists, "cid": c.cid,
                    "molecular_formula": c.molecular_formula,
                    "molecular_weight": c.molecular_weight,
                    "iupac_name": c.iupac_name,
                    "pubchem_url": c.pubchem_url, "error": c.error,
                }
                for c in self.chemicals
            ],
            "references": [
                {
                    "index": r.index, "authors": r.authors, "title": r.title,
                    "doi": r.doi, "year": r.year, "verified": r.verified,
                    "status": r.status, "crossref_title": r.crossref_title,
                    "error": r.error,
                    "abstract": r.abstract, "key_findings": r.key_findings,
                    "consistency_score": round(r.consistency_score, 3),
                    "consistency_detail": r.consistency_detail,
                    "deep_verified": r.deep_verified,
                    "fidelity_label": r.fidelity_label,
                    "distortion_detail": r.distortion_detail,
                    "search_method": r.search_method,
                }
                for r in self.references
            ],
            "numerical_checks": [
                {
                    "claim": n.claim, "quantity": n.quantity,
                    "value": n.value, "unit": n.unit,
                    "is_reasonable": n.is_reasonable,
                    "known_range": n.known_range, "explanation": n.explanation,
                }
                for n in self.numerical_checks
            ],
            "llm_analysis": self.llm_analysis,
            "overall_verdict": self.overall_verdict,
            "verified_at": self.verified_at,
        }

    def to_html(self) -> str:
        """生成 HTML 格式的验证报告"""
        parts = []

        # 总览
        score = self.credibility_score
        color = "#27ae60" if score >= 7 else ("#f39c12" if score >= 4 else "#e74c3c")
        parts.append(
            f"<h3 style='color:{color};'>"
            f"Verification Report — Credibility: {score}/10</h3>"
        )
        parts.append(
            f"<p>Total checks: {self.total_checks} | "
            f"<span style='color:#27ae60;'>Passed: {self.passed_checks}</span> | "
            f"<span style='color:#e74c3c;'>Failed: {self.failed_checks}</span> | "
            f"<span style='color:#f39c12;'>Warnings: {self.warning_checks}</span></p>"
        )
        parts.append("<hr>")

        # 1. 化学物质验证
        if self.chemicals:
            parts.append("<h4>1. Chemical Substance Verification (PubChem)</h4>")
            for c in self.chemicals:
                if c.exists:
                    parts.append(
                        f"<p style='color:#27ae60;'>"
                        f"<b>[PASS]</b> <b>{c.name}</b> "
                        f"— CID: {c.cid} | Formula: {c.molecular_formula} | "
                        f"MW: {c.molecular_weight} g/mol</p>"
                    )
                else:
                    parts.append(
                        f"<p style='color:#e74c3c;'>"
                        f"<b>[FAIL]</b> <b>{c.name}</b> "
                        f"— Not found in PubChem. {c.error}</p>"
                    )
            parts.append("<hr>")

        # 2. 参考文献验证
        if self.references:
            parts.append("<h4>2. Reference Verification (CrossRef + Semantic Scholar)</h4>")
            for r in self.references:
                if r.verified:
                    parts.append(
                        f"<p style='color:#27ae60;'>"
                        f"<b>[VERIFIED]</b> [{r.index}] {r.authors}, <i>{r.title}</i> "
                        f"({r.year})</p>"
                    )
                    if r.crossref_title:
                        parts.append(
                            f"<p style='margin-left:12px; font-size:11px; color:#555;'>"
                            f"CrossRef match: {r.crossref_title}</p>"
                        )
                elif "DOI" in r.status and "无法" in r.status:
                    parts.append(
                        f"<p style='color:#e74c3c;'>"
                        f"<b>[DOI INVALID]</b> [{r.index}] {r.authors}, "
                        f"<i>{r.title}</i> — DOI: {r.doi} is not resolvable</p>"
                    )
                else:
                    parts.append(
                        f"<p style='color:#f39c12;'>"
                        f"<b>[UNCHECKED]</b> [{r.index}] {r.authors}, "
                        f"<i>{r.title}</i> — {r.status}</p>"
                    )
                # 深度验证结果
                if r.deep_verified:
                    conf_color = "#27ae60" if r.consistency_score >= 0.7 else (
                        "#f39c12" if r.consistency_score >= 0.4 else "#e74c3c")
                    search_tag = ""
                    if r.search_method == "title":
                        search_tag = " <span style='background:#fff3cd;padding:1px 4px;border-radius:3px;font-size:10px;'>标题搜索</span>"
                    parts.append(
                        f"<p style='margin-left:12px; font-size:11px; color:{conf_color};'>"
                        f"📖 深度验证{search_tag}: 一致性={r.consistency_score:.0%} — "
                        f"{r.consistency_detail}</p>"
                    )
                    if r.key_findings:
                        parts.append(
                            f"<p style='margin-left:12px; font-size:11px; color:#555;'>"
                            f"关键结论: {r.key_findings[:200]}{'...' if len(r.key_findings) > 200 else ''}</p>"
                        )
                    # 保真度标签渲染
                    if r.fidelity_label:
                        fidelity_colors = {
                            "忠实": "#27ae60",
                            "轻微偏差": "#f39c12",
                            "过度解读": "#e67e22",
                            "严重歪曲": "#e74c3c",
                            "无法判定": "#95a5a6",
                        }
                        f_color = fidelity_colors.get(r.fidelity_label, "#95a5a6")
                        parts.append(
                            f"<p style='margin-left:12px; font-size:11px;'>"
                            f"<span style='color:{f_color};font-weight:600;'>"
                            f"🏷️ 保真度: {r.fidelity_label}</span>"
                        )
                        if r.distortion_detail:
                            parts.append(
                                f"<span style='color:#888;margin-left:6px;'>"
                                f"— {r.distortion_detail}</span>"
                            )
                        parts.append("</p>")
            parts.append("<hr>")

        # 3. 数值合理性检查
        if self.numerical_checks:
            parts.append("<h4>3. Numerical Plausibility Check</h4>")
            for n in self.numerical_checks:
                if n.is_reasonable:
                    parts.append(
                        f"<p style='color:#27ae60;'>"
                        f"<b>[REASONABLE]</b> {n.quantity}: {n.value} {n.unit} "
                        f"— Known range: {n.known_range}</p>"
                    )
                else:
                    parts.append(
                        f"<p style='color:#e74c3c;'>"
                        f"<b>[SUSPICIOUS]</b> {n.quantity}: {n.value} {n.unit} "
                        f"— Known range: {n.known_range}. {n.explanation}</p>"
                    )
            parts.append("<hr>")

        # 4. LLM 综合分析
        if self.llm_analysis:
            parts.append("<h4>4. AI Comprehensive Analysis</h4>")
            parts.append(f"<p style='color:#444; line-height:1.7;'>{self.llm_analysis}</p>")
            parts.append("<hr>")

        # 总体评价
        if self.overall_verdict:
            parts.append(f"<h4>Overall Verdict</h4>")
            parts.append(f"<p style='font-weight:bold; color:{color};'>{self.overall_verdict}</p>")

        return "".join(parts)


# ─────────────────────────────────────────────────────────────
# 验证器核心
# ─────────────────────────────────────────────────────────────

class ResultVerifier:
    """
    假设结果自动验证器。

    对 AI 生成的科学假设进行多维度真实数据验证。
    """

    def __init__(self, config: Optional[LLMConfig] = None):
        self.config = _ensure_config(config)

    @staticmethod
    def _to_str(v, fallback="N/A") -> str:
        """安全转字符串，处理 list/dict/None"""
        if v is None or (isinstance(v, str) and not v.strip()):
            return fallback
        if isinstance(v, list):
            return "\n".join(str(x) for x in v) if v else fallback
        if isinstance(v, dict):
            return json.dumps(v, ensure_ascii=False)
        return str(v)

    # ── Step 1: 化学物质验证 ──

    def verify_chemicals(self, hypothesis_data: dict) -> List[ChemicalVerification]:
        """
        从假设中提取化学品名称，并通过 PubChem API 验证其存在性。
        """
        from chem_structure import query_chem_info, extract_chemical_names

        # 从假设全文中提取化学品名称
        full_text = json.dumps(hypothesis_data, ensure_ascii=False)
        # 先用正则快速提取
        chem_names = extract_chemical_names(full_text)
        # 去重并限制数量（最多验证 15 个）
        seen = set()
        unique_names = []
        for name in chem_names:
            if name.lower() not in seen:
                seen.add(name.lower())
                unique_names.append(name)
            if len(unique_names) >= 15:
                break

        results = []
        for name in unique_names:
            try:
                info = query_chem_info(name)
                if info is not None:
                    results.append(ChemicalVerification(
                        name=name,
                        exists=True,
                        cid=info.cid,
                        molecular_formula=info.molecular_formula,
                        molecular_weight=info.molecular_weight,
                        iupac_name=info.iupac_name,
                        pubchem_url=info.pubchem_url,
                    ))
                else:
                    results.append(ChemicalVerification(
                        name=name,
                        exists=False,
                        error="No matching compound found in PubChem database",
                    ))
            except Exception as e:
                results.append(ChemicalVerification(
                    name=name,
                    exists=False,
                    error=str(e),
                ))

        return results

    # ── Step 2: 参考文献验证 ──

    def verify_references(self, hypothesis_data: dict) -> List[ReferenceVerification]:
        """
        通过 CrossRef API 验证参考文献的 DOI 是否有效。
        """
        refs = hypothesis_data.get("references", [])
        results = []

        for i, ref in enumerate(refs, 1):
            if not isinstance(ref, dict):
                result = ReferenceVerification(
                    index=i,
                    title=str(ref),
                )
                result.status = "非标准格式，跳过验证"
                result.verified = False
                results.append(result)
                continue

            doi = ref.get("doi", "") or ""
            title = ref.get("title", "") or ""
            authors = ref.get("authors", "") or ""
            year = ref.get("year", 0)

            result = ReferenceVerification(
                index=i,
                authors=authors,
                title=title,
                doi=doi,
                year=year,
            )

            if not doi:
                result.status = "No DOI provided"
                result.verified = False
                results.append(result)
                continue

            try:
                # 通过 CrossRef API 查询 DOI
                url = f"https://api.crossref.org/works/{doi}"
                resp = requests.get(
                    url,
                    headers={"User-Agent": "Chalk/AI-Scientist (mailto:chalk@research.org)"},
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json().get("message", {})
                    cr_title = data.get("title", [""])[0] if data.get("title") else ""
                    result.verified = True
                    result.status = "DOI valid"
                    result.crossref_title = cr_title
                else:
                    result.verified = False
                    result.status = f"DOI not resolvable (HTTP {resp.status_code})"
            except requests.exceptions.Timeout:
                result.status = "CrossRef API timeout"
            except Exception as e:
                result.status = f"Verification error: {str(e)[:80]}"

            results.append(result)

        return results

    # ── Step 2.5: 深度参考文献验证 (P0: 增强) ──

    def deep_verify_references(
        self,
        references: List[ReferenceVerification],
        hypothesis_data: dict,
    ) -> List[ReferenceVerification]:
        """
        对参考文献进行深度验证，支持三种查找路径：
          1. 有 DOI → 通过 Semantic Scholar DOI 查询
          2. 无 DOI → 通过标题在 Semantic Scholar 搜索
          3. 有摘要 → 直接 LLM 一致性检查
          4. 无摘要 → LLM 从标题+期刊推断核心结论（标记低置信度）

        新增输出：
          - fidelity_label: 忠实/轻微偏差/过度解读/严重歪曲/无法判定
          - distortion_detail: 偏差具体说明
          - search_method: DOI/title/none

        Args:
            references: 已完成 Step 2 DOI 验证的参考文献列表
            hypothesis_data: 原始假设数据（用于提取引用上下文）

        Returns:
            更新后的 ReferenceVerification 列表
        """
        try:
            from knowledge_base import ExternalKnowledgeAdapter
        except ImportError:
            logger.warning("knowledge_base 不可用，跳过深度参考文献验证")
            return references

        # 从假设全文中提取引用上下文
        hypothesis_text = json.dumps(hypothesis_data, ensure_ascii=False)

        for ref in references:
            # ── 跳过非标准格式引用 ──
            if ref.status == "非标准格式，跳过验证":
                continue

            try:
                paper_info = None
                abstract = ""
                is_title_search = False
                is_llm_inferred = False

                # ── 路径1: 有 DOI → Semantic Scholar DOI 查询 ──
                if ref.doi and ref.verified:
                    paper_info = ExternalKnowledgeAdapter.query_paper(ref.doi)
                    if paper_info:
                        ref.search_method = "DOI"
                        abstract = paper_info.get("abstract", "") or ""

                # ── 路径2: 无 DOI 或 DOI 查询失败 → 标题搜索回退 ──
                if not paper_info and ref.title:
                    paper_info = ExternalKnowledgeAdapter.query_paper_by_title(
                        ref.title, year=ref.year or 0
                    )
                    if paper_info:
                        ref.search_method = "title"
                        is_title_search = True
                        abstract = paper_info.get("abstract", "") or ""
                        # 如果标题搜索找到了DOI，更新
                        if not ref.doi and paper_info.get("externalIds", {}).get("DOI"):
                            ref.doi = paper_info["externalIds"]["DOI"]

                # ── 没找到任何论文信息 ──
                if not paper_info:
                    ref.consistency_detail = "无法通过DOI或标题找到该论文"
                    ref.fidelity_label = "无法判定"
                    ref.search_method = "none"
                    continue

                # ── 路径3: 有摘要 → 正常 LLM 一致性检查 ──
                if abstract:
                    ref.abstract = abstract
                else:
                    # ── 路径4: 无摘要 → LLM 从元数据推断核心结论 ──
                    is_llm_inferred = True
                    meta_info = f"标题: {ref.title}"
                    if ref.authors:
                        meta_info += f"; 作者: {ref.authors}"
                    if ref.year:
                        meta_info += f"; 年份: {ref.year}"
                    journal = (paper_info.get("journal") or "")
                    if journal:
                        meta_info += f"; 期刊: {journal}"

                    llm_inference = self._llm_infer_findings(meta_info)
                    if llm_inference:
                        ref.abstract = f"[LLM推断] {llm_inference}"
                        ref.key_findings = llm_inference
                    else:
                        ref.consistency_detail = "该论文无公开摘要，LLM推断亦失败"
                        ref.fidelity_label = "无法判定"
                        continue

                # ── 提取引用上下文 ──
                ref_context = self._extract_citation_context(ref, hypothesis_text)

                # ── LLM 一致性检查 ──
                check_abstract = abstract if abstract else ref.abstract
                llm_result = self._llm_consistency_check(
                    check_abstract, ref_context, ref.title,
                    is_llm_inferred=is_llm_inferred,
                )

                if llm_result:
                    if not ref.key_findings:
                        ref.key_findings = llm_result.get("key_findings", "")
                    ref.consistency_score = float(llm_result.get("consistency_score", 0.0))
                    ref.consistency_detail = llm_result.get("consistency_detail", "")
                    ref.fidelity_label = llm_result.get("fidelity_label", "无法判定")
                    ref.distortion_detail = llm_result.get("distortion_detail", "")
                    ref.deep_verified = True

                    # 标题搜索和无摘要推断降低 consistency_score 权重
                    if is_title_search:
                        ref.consistency_score *= 0.9  # 标题匹配可能有误
                        ref.consistency_detail = "[标题搜索] " + ref.consistency_detail
                    if is_llm_inferred:
                        ref.consistency_score *= 0.7  # LLM推断摘要不可靠
                        ref.consistency_detail = "[摘要推断] " + ref.consistency_detail

            except Exception as e:
                logger.warning(f"深度验证参考文献 [{ref.index}] 失败: {e}")
                ref.consistency_detail = f"深度验证失败: {str(e)[:100]}"
                ref.fidelity_label = "无法判定"

        return references

    @staticmethod
    def _extract_citation_context(ref: ReferenceVerification, text: str) -> str:
        """
        从假设全文中提取某篇参考文献的引用上下文。

        搜索模式：[index], (Authors, Year), 或 DOI
        返回标记前后各 200 字符。
        """
        contexts = []

        # 模式1: [数字] 引用
        bracket_pattern = f"[{ref.index}]"
        idx = text.find(bracket_pattern)
        if idx != -1:
            start = max(0, idx - 200)
            end = min(len(text), idx + len(bracket_pattern) + 200)
            contexts.append(text[start:end])

        # 模式2: (Author, Year)
        if ref.authors and ref.year:
            # 取第一作者姓
            first_author = ref.authors.split(",")[0].split(" et")[0].strip()
            if first_author:
                year_pattern = f"({first_author}, {ref.year})"
                idx = text.find(year_pattern)
                if idx == -1:
                    year_pattern = f"({first_author} et al., {ref.year})"
                    idx = text.find(year_pattern)
                if idx != -1:
                    start = max(0, idx - 200)
                    end = min(len(text), idx + len(year_pattern) + 200)
                    contexts.append(text[start:end])

        # 模式3: DOI
        if ref.doi:
            idx = text.find(ref.doi)
            if idx != -1:
                start = max(0, idx - 200)
                end = min(len(text), idx + len(ref.doi) + 200)
                contexts.append(text[start:end])

        return " ... ".join(contexts) if contexts else ""

    def _llm_consistency_check(
        self,
        abstract: str,
        citation_context: str,
        paper_title: str,
        is_llm_inferred: bool = False,
    ) -> Optional[dict]:
        """
        用 LLM 评估引用上下文与论文摘要的一致性，并输出保真度标签。

        Args:
            abstract: 论文摘要
            citation_context: 假设中引用该论文的上下文
            paper_title: 论文标题
            is_llm_inferred: 摘要是否为 LLM 推断（影响评分提示）

        Returns:
            {"key_findings": str, "consistency_score": float, "consistency_detail": str,
             "fidelity_label": str, "distortion_detail": str}
        """
        # 截断过长的摘要
        abstract_trimmed = abstract[:1500] if len(abstract) > 1500 else abstract
        context_trimmed = citation_context[:800] if len(citation_context) > 800 else citation_context

        context_section = ""
        if context_trimmed:
            context_section = (
                f"\n\n【引用上下文】（假设中引用该论文的段落）:\n{context_trimmed}\n"
            )
        else:
            context_section = "\n\n【引用上下文】: 未在假设中找到明确的引用段落。\n"

        inferred_note = ""
        if is_llm_inferred:
            inferred_note = (
                "\n⚠️ 注意：该论文无公开摘要，以下\"摘要\"是基于标题和元数据由AI推断的，"
                "可靠性较低。请在评分时适当降低权重。\n"
            )

        prompt_header = (
            "你是一位严谨的科学文献审查专家。请分析以下论文摘要，"
            "并与假设中的引用上下文进行一致性和保真度评估。\n\n"
            f"【论文标题】: {paper_title}\n\n"
            f"【论文摘要】:\n{abstract_trimmed}\n"
        )
        prompt_rules = (
            "请以 JSON 格式输出（不加 markdown 代码块）：\n"
            "{\n"
            '  "key_findings": "从摘要中提取的2-3个关键结论（中文）",\n'
            '  "consistency_score": 0.8,\n'
            '  "consistency_detail": "一致性评价说明",\n'
            '  "fidelity_label": "忠实|轻微偏差|过度解读|严重歪曲|无法判定",\n'
            '  "distortion_detail": "偏差的具体说明（如无偏差则为空字符串）"\n'
            "}\n\n"
            "评分规则：\n"
            "1. consistency_score 范围 0-1：\n"
            "   0.8-1.0: 引用准确反映了论文结论\n"
            "   0.5-0.7: 部分一致，有轻微偏差或过度解读\n"
            "   0.2-0.4: 存在明显不一致或断章取义\n"
            "   0.0-0.1: 引用与论文结论严重矛盾\n"
            "2. 如果没有引用上下文，仅基于摘要提取 key_findings，consistency_score 设为 0.5\n"
            "3. key_findings 用中文概括\n"
            "4. fidelity_label 必须是以下之一：\n"
            "   - 忠实：引用准确传达了论文核心结论\n"
            "   - 轻微偏差：引用基本正确但有小范围的外推或简化\n"
            "   - 过度解读：引用将论文结论推广到论文未涉及的领域\n"
            "   - 严重歪曲：引用与论文结论矛盾或断章取义\n"
            "   - 无法判定：信息不足，无法评估\n"
            "5. distortion_detail 在 fidelity_label 为轻微偏差/过度解读/严重歪曲时必须填写，"
            "说明具体哪句话被歪曲、如何过度解读等；fidelity_label 为忠实或无法判定时为空字符串\n"
        )
        prompt = prompt_header + inferred_note + context_section + prompt_rules

        try:
            result = _chat(prompt, self.config, task="compare", timeout=120, retries=2)
            result = result.strip()
            if result.startswith("```"):
                result = result.split("\n", 1)[1].rsplit("```", 1)[0].strip()

            parsed = json.loads(result)
            if isinstance(parsed, dict):
                # 钳制 consistency_score 到 [0, 1]
                score = float(parsed.get("consistency_score", 0.5))
                score = max(0.0, min(1.0, score))
                parsed["consistency_score"] = score
                # 校验 fidelity_label
                valid_labels = {"忠实", "轻微偏差", "过度解读", "严重歪曲", "无法判定"}
                label = parsed.get("fidelity_label", "无法判定")
                if label not in valid_labels:
                    parsed["fidelity_label"] = "无法判定"
                # 确保 distortion_detail 存在
                if "distortion_detail" not in parsed:
                    parsed["distortion_detail"] = ""
                return parsed
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning(f"深度验证 LLM 输出解析失败: {e}")
        except Exception as e:
            logger.warning(f"深度验证 LLM 调用失败: {e}")

        return None

    def _llm_infer_findings(self, meta_info: str) -> str:
        """
        当论文无公开摘要时，用 LLM 从标题+元数据推断核心结论。

        Args:
            meta_info: 格式化的论文元数据（标题/作者/年份/期刊）

        Returns:
            推断的核心结论文本（中文），失败返回空字符串
        """
        prompt = (
            "你是一名材料科学与化学领域的资深研究者。以下论文缺少公开摘要，"
            "请根据论文标题和元数据推断其最可能的核心研究内容和结论。\n\n"
            f"【论文元数据】:\n{meta_info}\n\n"
            "请用2-3句话概括该论文最可能的研究内容和核心结论（中文）。"
            "只输出概括文本，不要加任何标记或代码块。"
            "如果你对该论文完全无法推断，请输出\"无法推断\"。"
        )

        try:
            result = _chat(prompt, self.config, task="qa", timeout=60, retries=1)
            result = result.strip()
            if result and result != "无法推断" and len(result) > 10:
                return result
        except Exception as e:
            logger.warning(f"LLM 推断论文结论失败: {e}")

        return ""

    # ── Step 3: 数值合理性检查 ──

    def check_numerical_claims(self, hypothesis_data: dict) -> List[NumericalCheck]:
        """
        基于 NIST/CRC 已知物理化学常数范围，检查假设中的数值预测是否合理。
        """
        checks = []

        # 已知的常见物理化学量合理范围 (单位统一)
        KNOWN_RANGES = {
            # 催化/电化学
            "activation energy": {"range": (0.1, 5.0), "unit": "eV",
                                  "desc": "Typical catalytic activation energies"},
            "overpotential": {"range": (0.1, 1.5), "unit": "V",
                              "desc": "Typical ORR/OER overpotentials"},
            "onset potential": {"range": (0.3, 1.2), "unit": "V vs RHE",
                                "desc": "ORR onset potential range"},
            "half-wave potential": {"range": (0.5, 0.95), "unit": "V vs RHE",
                                    "desc": "ORR half-wave potential range"},
            "limiting current density": {"range": (2.0, 8.0), "unit": "mA/cm2",
                                         "desc": "ORR limiting current density"},
            "electron transfer number": {"range": (2.0, 4.0), "unit": "",
                                         "desc": "ORR electron transfer number"},
            "tafel slope": {"range": (30, 120), "unit": "mV/dec",
                            "desc": "Typical Tafel slope range"},
            # 热力学
            "binding energy": {"range": (0.1, 10.0), "unit": "eV",
                               "desc": "Adsorbate binding energy on surfaces"},
            "adsorption energy": {"range": (0.1, 8.0), "unit": "eV",
                                  "desc": "Typical adsorption energies"},
            "formation energy": {"range": (-5.0, 5.0), "unit": "eV/atom",
                                 "desc": "Compound formation energies"},
            "band gap": {"range": (0.0, 6.0), "unit": "eV",
                         "desc": "Semiconductor band gap range"},
            "d-band center": {"range": (-3.0, 0.5), "unit": "eV",
                              "desc": "d-band center relative to Fermi level"},
            "work function": {"range": (2.0, 6.0), "unit": "eV",
                              "desc": "Metal work function range"},
            # 动力学/扩散
            "diffusion coefficient": {"range": (1e-16, 1e-6), "unit": "cm2/s",
                                      "desc": "Solid-state diffusion coefficient"},
            "conductivity": {"range": (1e-10, 1e6), "unit": "S/cm",
                             "desc": "Electrical conductivity range"},
            # 结构
            "lattice constant": {"range": (2.0, 15.0), "unit": "Angstrom",
                                 "desc": "Crystal lattice constant range"},
            "interlayer distance": {"range": (3.0, 10.0), "unit": "Angstrom",
                                    "desc": "Layered material interlayer distance"},
            # 性能
            "faradaic efficiency": {"range": (50, 100), "unit": "%",
                                    "desc": "Faradaic efficiency range"},
            "coulombic efficiency": {"range": (80, 100), "unit": "%",
                                     "desc": "Coulombic efficiency range"},
            "specific capacity": {"range": (50, 3000), "unit": "mAh/g",
                                  "desc": "Battery specific capacity range"},
            "conversion efficiency": {"range": (1, 30), "unit": "%",
                                      "desc": "Catalytic conversion efficiency"},
        }

        # 从 expected_results 和 methods 中提取数值
        text_fields = [
            hypothesis_data.get("expected_results", ""),
            hypothesis_data.get("methods", ""),
            hypothesis_data.get("rationale", ""),
            hypothesis_data.get("technical_details", ""),
        ]
        # 确保所有字段都是字符串
        safe_fields = []
        for t in text_fields:
            if isinstance(t, list):
                safe_fields.append("\n".join(str(x) for x in t))
            elif isinstance(t, str) and t.strip():
                safe_fields.append(t)
        full_text = " ".join(safe_fields)

        # 数值+单位提取模式
        num_patterns = [
            # eV 模式
            r'([\d.]+)\s*(?:eV|electron\s*volt)',
            # V 模式
            r'([\d.]+)\s*(?:V\s*vs\s*RHE|V\s*vs\s*SCE|V\s*vs\s*Ag/AgCl|mV(?:/dec)?)',
            # mA/cm2 模式
            r'([\d.]+)\s*(?:mA/cm[²2]|mA\s*cm[-−]?[²2])',
            # % 模式
            r'([\d.]+)\s*(?:%|percent)',
            # Angstrom 模式
            r'([\d.]+)\s*(?:Å|Angstrom|angstrom)',
            # mAh/g 模式
            r'([\d.]+)\s*(?:mAh/g|mAh\s*g[-−]?1)',
            # S/cm 模式
            r'([\d.]+)\s*(?:S/cm|S\s*cm[-−]?1)',
            # cm2/s 模式（科学计数法）
            r'([\d.eE+-]+)\s*(?:cm[²2]/s|cm[²2]\s*s[-−]?1)',
            # 通用能量 (eV)
            r'(?:energy|potential|gap|center)\s+(?:of|is|:)\s*([\d.]+-?[\d.]*)\s*eV',
        ]

        # 用关键词定位物理量类型
        text_lower = full_text.lower()
        for keyword, info in KNOWN_RANGES.items():
            keyword_lower = keyword.lower()

            if keyword_lower not in text_lower:
                continue

            # 在关键词附近寻找数值
            # 扩展搜索范围：取关键词前后 100 字符
            for m in re.finditer(re.escape(keyword_lower), text_lower):
                start = max(0, m.start() - 80)
                end = min(len(full_text), m.end() + 80)
                context = full_text[start:end]

                # 尝试提取数值
                for pat in num_patterns:
                    for nm in re.finditer(pat, context, re.I):
                        try:
                            val_str = nm.group(1)
                            # 处理科学计数法
                            val = float(val_str)
                        except (ValueError, IndexError):
                            continue

                        lo, hi = info["range"]

                        # 特殊处理：科学计数法
                        if val < 1e-5 and lo > 0:
                            is_reasonable = lo <= val <= hi
                        elif val > 1000:
                            is_reasonable = lo <= val <= hi
                        else:
                            is_reasonable = lo <= val <= hi

                        explanation = ""
                        if not is_reasonable:
                            explanation = (
                                f"The claimed value ({val} {info['unit']}) "
                                f"falls outside the known range "
                                f"({lo}–{hi} {info['unit']}). "
                                f"This may indicate an error or unrealistic prediction."
                            )

                        checks.append(NumericalCheck(
                            claim=f"{keyword}: {val} {info['unit']}",
                            quantity=keyword,
                            value=val,
                            unit=info["unit"],
                            is_reasonable=is_reasonable,
                            known_range=f"{lo}–{hi} {info['unit']}",
                            explanation=explanation,
                        ))
                        break  # 每个物理量只检查一次
                    else:
                        continue
                    break

        return checks

    # ── Step 4: LLM 综合分析 ──

    def llm_analyze(self, hypothesis_data: dict, report: VerificationReport) -> str:
        """
        让 LLM 结合验证数据进行综合分析，生成可信度评估。
        """
        # 构造验证数据摘要
        chem_summary = ""
        if report.chemicals:
            for c in report.chemicals:
                status = "VERIFIED" if c.exists else "NOT FOUND"
                detail = f"Formula: {c.molecular_formula}" if c.exists else f"Error: {c.error}"
                chem_summary += f"- {c.name}: [{status}] {detail}\n"

        ref_summary = ""
        if report.references:
            for r in report.references:
                status = "VERIFIED" if r.verified else f"UNVERIFIED ({r.status})"
                ref_summary += f"- [{r.index}] {r.authors}, {r.title} ({r.year}): [{status}]\n"
                if r.deep_verified:
                    ref_summary += f"  Deep verification: consistency={r.consistency_score:.0%} — {r.consistency_detail}\n"
                    if r.key_findings:
                        ref_summary += f"  Key findings: {r.key_findings}\n"

        num_summary = ""
        if report.numerical_checks:
            for n in report.numerical_checks:
                status = "REASONABLE" if n.is_reasonable else "SUSPICIOUS"
                num_summary += f"- {n.claim}: [{status}] (Known range: {n.known_range})\n"

        prompt = (
            "You are a rigorous scientific verification expert. Based on the following "
            "multi-source verification data, analyze the credibility of this scientific hypothesis.\n\n"
            f"【Hypothesis Title】: {self._to_str(report.hypothesis_title, 'Untitled')}\n\n"
            f"【Chemical Substance Verification (PubChem)】:\n{chem_summary or 'No chemicals extracted for verification.'}\n\n"
            f"【Reference Verification (CrossRef DOI)】:\n{ref_summary or 'No references to verify.'}\n\n"
            f"【Numerical Plausibility Check】:\n{num_summary or 'No numerical claims extracted.'}\n\n"
            f"【Original Hypothesis (Expected Results)】:\n{self._to_str(hypothesis_data.get('expected_results', 'N/A'))}\n\n"
            f"【Original Hypothesis (Methods)】:\n{self._to_str(hypothesis_data.get('methods', 'N/A'))}\n\n"
            "Please provide a comprehensive analysis in the following JSON format "
            "(no markdown code blocks, return raw JSON):\n"
            "{\n"
            '  "credibility_score": 7,\n'
            '  "overall_verdict": "Overall assessment of the hypothesis credibility...",\n'
            '  "chemical_analysis": "Analysis of chemical substance verification results...",\n'
            '  "reference_analysis": "Analysis of reference verification results...",\n'
            '  "numerical_analysis": "Analysis of numerical plausibility...",\n'
            '  "key_findings": ["Key finding 1", "Key finding 2"],\n'
            '  "recommendations": ["Recommendation 1", "Recommendation 2"]\n'
            "}\n\n"
            "Rules:\n"
            "1. credibility_score is 1-10 (10 = highly credible with strong evidence)\n"
            "2. Be honest — if verification failed, clearly state it\n"
            "3. Distinguish between 'not verified' (inconclusive) and 'verified false'\n"
            "4. recommendations should be actionable suggestions for improving the hypothesis\n"
        )

        try:
            result = _chat(prompt, self.config, task="compare", timeout=120)
            return result.strip()
        except Exception as e:
            return f"LLM analysis failed: {e}"

    # ── 主验证流程 ──

    def verify(
        self,
        hypothesis_data: dict,
        on_progress: Optional[Callable[[str, int, int], None]] = None,
    ) -> VerificationReport:
        """
        执行完整的验证流程。

        Args:
            hypothesis_data: 假设输出的 JSON 字典
            on_progress: 进度回调 (stage_name, current_step, total_steps)

        Returns:
            VerificationReport
        """
        from datetime import datetime, timezone

        report = VerificationReport(
            hypothesis_title=self._to_str(hypothesis_data.get("paper_title", "Untitled"), "Untitled"),
            verified_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        )

        total_steps = 5
        step = 0

        # Step 1: 化学物质验证
        step += 1
        if on_progress:
            on_progress("Verifying chemical substances (PubChem)", step, total_steps)
        report.chemicals = self.verify_chemicals(hypothesis_data)

        # Step 2: 参考文献验证
        step += 1
        if on_progress:
            on_progress("Verifying references (CrossRef DOI)", step, total_steps)
        report.references = self.verify_references(hypothesis_data)

        # Step 2.5: 深度参考文献验证 (P1: ReferenceDeepVerifier)
        step += 1
        if on_progress:
            on_progress("Deep verifying references (Semantic Scholar + LLM)", step, total_steps)
        report.references = self.deep_verify_references(report.references, hypothesis_data)

        # Step 3: 数值合理性检查
        step += 1
        if on_progress:
            on_progress("Checking numerical plausibility", step, total_steps)
        report.numerical_checks = self.check_numerical_claims(hypothesis_data)

        # Step 4: LLM 综合分析
        step += 1
        if on_progress:
            on_progress("AI comprehensive analysis", step, total_steps)
        llm_result = self.llm_analyze(hypothesis_data, report)
        report.llm_analysis = llm_result

        # 尝试解析 LLM 输出
        try:
            llm_data = self._parse_json_raw(llm_result)
            report.credibility_score = llm_data.get("credibility_score", 5)
            report.overall_verdict = llm_data.get("overall_verdict", "")
        except Exception:
            report.credibility_score = 5
            report.overall_verdict = "Unable to parse AI analysis"

        # 统计
        for c in report.chemicals:
            report.total_checks += 1
            if c.exists:
                report.passed_checks += 1
            else:
                report.failed_checks += 1

        for r in report.references:
            report.total_checks += 1
            if r.verified:
                report.passed_checks += 1
            elif "DOI" in r.status and ("无法" in r.status or "not" in r.status.lower()):
                report.failed_checks += 1
            else:
                report.warning_checks += 1
            # 深度验证统计
            if r.deep_verified:
                report.total_checks += 1
                if r.consistency_score >= 0.7:
                    report.passed_checks += 1
                elif r.consistency_score >= 0.4:
                    report.warning_checks += 1
                else:
                    report.failed_checks += 1

        for n in report.numerical_checks:
            report.total_checks += 1
            if n.is_reasonable:
                report.passed_checks += 1
            else:
                report.warning_checks += 1

        if on_progress:
            on_progress("Verification complete", total_steps, total_steps)

        return report

    @staticmethod
    def _parse_json_raw(text: str) -> dict:
        """容错 JSON 解析"""
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            for start_char, end_char in [("{", "}"), ("[", "]")]:
                start = text.find(start_char)
                end = text.rfind(end_char)
                if start != -1 and end != -1 and end > start:
                    try:
                        return json.loads(text[start:end + 1])
                    except json.JSONDecodeError:
                        continue
            return {"raw_text": text, "parse_error": True}


# ─────────────────────────────────────────────────────────────
# 便捷函数
# ─────────────────────────────────────────────────────────────

def verify_hypothesis(
    hypothesis_data: dict,
    config: Optional[LLMConfig] = None,
    on_progress: Optional[Callable[[str, int, int], None]] = None,
) -> VerificationReport:
    """
    便捷函数：验证假设结果的可靠性。

    Args:
        hypothesis_data: 假设输出的字典
        config: LLM 配置
        on_progress: 进度回调

    Returns:
        VerificationReport
    """
    verifier = ResultVerifier(config=config)
    return verifier.verify(hypothesis_data, on_progress=on_progress)
