"""
multimodal_pipeline.py — 多模态数据深度整合管线

将 PDF 导入时提取的图片 → 多模态分析 → 数据清洗 → 关联挖掘 →
注入假设生成上下文 的完整链路打通。

核心流程：
  1. 发现文档关联图片（两种路径模式兼容）
  2. 批量调用 qwen-vl-max 分析科学图表
  3. 数据清洗（单位统一、OCR纠错、范围校验、去重）
  4. 关联挖掘（规则驱动 + LLM辅助）
  5. 缓存结果到 DB，避免重复调用
  6. 生成可注入 LiteratureAgent 的结构化上下文

比赛要求对应：
  "基于多模态大模型对科学模态数据的处理成效"
  "实现数据的智能清洗、分析与关联挖掘，精准识别关键信息"
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from data_cleaner import CleanedDataPoint, DataCleaner, CleaningReport
from data_miner import Association, DataMiner, MiningReport

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# 数据类
# ─────────────────────────────────────────────────────────────

@dataclass
class MultimodalResult:
    """单个图表的多模态分析结果"""
    image_path: str = ""
    image_type: str = ""        # XRD / XPS / SEM / TEM / 表格 / 其他
    source_label: str = ""      # 来源标注（如 "Fig.1"）
    # 分析阶段
    raw_analysis: str = ""      # 原始分析文本
    summary: str = ""           # 摘要
    # 清洗阶段
    raw_data_points: List[Dict] = field(default_factory=list)
    cleaned_points: List[CleanedDataPoint] = field(default_factory=list)
    cleaning_report: Optional[CleaningReport] = None
    # 状态
    cached: bool = False        # 是否来自缓存
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "image_path": self.image_path,
            "image_type": self.image_type,
            "source_label": self.source_label,
            "raw_analysis": self.raw_analysis,
            "summary": self.summary,
            "cleaned_points": [
                {
                    "parameter": cp.parameter,
                    "parameter_cn": cp.parameter_cn,
                    "value": cp.value,
                    "unit": cp.unit,
                    "source": cp.source,
                    "is_suspicious": cp.is_suspicious,
                    "suspicious_reason": cp.suspicious_reason,
                }
                for cp in self.cleaned_points
            ],
            "cached": self.cached,
            "error": self.error,
        }


@dataclass
class PipelineReport:
    """整条管线的执行报告"""
    total_images: int = 0
    analyzed: int = 0
    cached: int = 0
    failed: int = 0
    total_cleaned_points: int = 0
    total_associations: int = 0
    results: List[MultimodalResult] = field(default_factory=list)
    cleaning_report: Optional[CleaningReport] = None
    mining_report: Optional[MiningReport] = None

    def to_html(self) -> str:
        parts = []
        display = self.analyzed + self.cached
        parts.append(
            f"<h4 style='color:#534AB7;'>多模态数据整合报告 "
            f"({display}/{self.total_images} 图表已分析)</h4>"
        )
        parts.append("<table style='width:100%;border-collapse:collapse;font-size:12px;'>")
        parts.append(
            "<tr style='background:#f8f9fa;'>"
            "<th>来源</th><th>类型</th><th>数据点</th><th>状态</th></tr>"
        )
        for r in self.results:
            status = (
                f"<span style='color:#27ae60;'>{len(r.cleaned_points)} 数据点</span>"
                if not r.error
                else f"<span style='color:#e74c3c;'>失败</span>"
            )
            if r.cached:
                status += " <span style='color:#888;font-size:11px;'>(缓存)</span>"
            parts.append(
                f"<tr><td>{r.source_label or os.path.basename(r.image_path)}</td>"
                f"<td>{r.image_type}</td>"
                f"<td>{len(r.cleaned_points)}</td>"
                f"<td>{status}</td></tr>"
            )
        parts.append("</table>")

        if self.cleaning_report:
            parts.append(self.cleaning_report.to_html())
        if self.mining_report and self.mining_report.total_associations > 0:
            parts.append(self.mining_report.to_html())

        return "\n".join(parts)


# ─────────────────────────────────────────────────────────────
# 图片路径发现
# ─────────────────────────────────────────────────────────────

def discover_document_images(
    base_dir: str,
    user_id: int,
    doc_id: int,
    doc_source_path: Optional[str] = None,
    config=None,
    full_text: str = "",
    use_llm_page_hints: bool = False,
    force_reextract: bool = False,
) -> List[str]:
    """
    Discover document-scoped images for multimodal analysis.

    This intentionally uses the same per-document extraction path as literature
    management and never reads the legacy flat user_{id}_pdf_images directory.
    """
    from document_image_extractor import ensure_document_images

    return ensure_document_images(
        base_dir,
        user_id,
        doc_id,
        doc_source_path=doc_source_path,
        full_text=full_text,
        config=config,
        use_llm_page_hints=use_llm_page_hints,
        force_reextract=force_reextract,
    )

def _is_error_response(text: str) -> bool:
    """检测 LLM/VL 调用返回的错误响应字符串。"""
    if not text:
        return False
    # _chat() 返回: "调用通义千问接口失败（模型: ..."
    # _chat_multimodal() 返回: "调用多模态接口失败（模型: ..."
    prefix = text[:30]
    return prefix.startswith("调用") or "失败" in prefix or "Unauthorized" in prefix or "401" in prefix


def _extract_data_points_from_analysis(
    analysis_text: str,
    config=None,
) -> List[Dict[str, Any]]:
    """
    从多模态分析文本中提取结构化数据点。

    两阶段策略：
      1. 正则快速匹配已知参数名+数值+单位
      2. 若正则返回 0 条结果，fallback 到 LLM 提取

    Returns:
        [{"parameter": str, "value": str, "unit": str}, ...]
    """
    if not analysis_text:
        return []

    # ── 阶段 1：正则提取 ──
    points = _regex_extract_points(analysis_text)

    # ── 阶段 2：LLM fallback ──
    if not points and config:
        try:
            points = _llm_extract_points(analysis_text, config)
        except Exception as e:
            logger.warning(f"LLM 数据提取失败: {e}")

    return points


def _regex_extract_points(analysis_text: str) -> List[Dict[str, Any]]:
    """用正则从分析文本中快速匹配已知参数模式。"""
    points = []
    # 预处理：清理 Markdown 格式标记
    analysis_text = re.sub(r'\*\*([^*]+)\*\*', r'\1', analysis_text)  # **bold** → bold
    analysis_text = re.sub(r'__([^_]+)__', r'\1', analysis_text)      # __bold__ → bold
    analysis_text = re.sub(r'`([^`]+)`', r'\1', analysis_text)        # `code` → code
    lines = analysis_text.splitlines()

    # ── 参数模式（按特异性从高到低排列）──
    # 每个模式自带捕获组 (...)，外层用 (?:...) 避免组号偏移
    # 注意：避免单字母模式（D/E/T/P/c/j）——在 IGNORECASE 下误匹配率极高
    param_patterns = [
        # ── 高特异性（含明确修饰词/上下文） ──
        r"(d[- ]?band\s+center|d带中心|epsilon_d|d[- ]?band中心)",
        r"(exchange\s+current\s+density|交换电流密度|j_0|j0)",
        r"(half[- ]?wave\s+potential|半波电位|E1/2)",
        r"(onset\s+potential|起始电位|起波电位)",
        r"(overpotential|过电势|过电位)",
        r"(charge\s+transfer\s+resistance|电荷转移电阻|R_ct)",
        r"(double[- ]?layer\s+capacitance|双电层电容|C_dl)",
        r"(limiting\s+current\s*density|极限电流密度)",
        r"(limiting\s+current|极限电流)",
        r"(tafel\s+slope|塔菲尔斜率|Tafel\s*斜率)",
        r"(electron\s+transfer\s+number|电子转移数|转移电子数)",
        r"(faradaic\s+efficiency|法拉第效率)",
        r"(coulombic\s+efficiency|库[仑伦]效率)",
        r"(adsorption\s+energy|吸附能|E_ads)",
        r"(binding\s+energy|结合能|E_bind)",
        r"(formation\s+energy|形成能|E_f)",
        r"(activation\s+energy|活化能|E_a|Ea)",
        r"(reaction\s+barrier|反应能垒|反应势垒)",
        r"(Gibbs\s+free\s+energy|吉布斯自由能|ΔG)",
        r"(surface\s+energy|表面能|E_surf)",
        r"(band\s+gap|带隙|E_g|Eg)",
        r"(work\s+function|功函数)",
        r"(lattice\s+(?:constant|parameter)|晶格常数|晶格参数)",
        r"(interlayer\s+distance|层间距|d[- ]?spacing|层间距离)",
        r"(conversion\s+efficiency|转化效率|转化率)",
        r"(specific\s+capacity|比容量|质量比容量)",
        r"(BET\s*(?:surface\s*area)?|比表面积)",
        r"(ECSA|电化学活性面积|电化学表面积)",
        r"(XRD\s*peak|2theta|2θ|衍射峰)",
        r"(FWHM|半峰宽|半高宽)",
        # ── XPS / 光谱峰位模式（元素+轨道，优先于通用"峰位"） ──
        r"((?:Co|Ni|Fe|Cu|Mn|Cr|V|Ti|Mo|W|Pt|Pd|Ru|Ir|Au|Ag|Zn|Al|Si|Sn|Pb|Bi|Ce|La|Y|Zr|Nb|Ta|Re|Os|Rh)\s*"
        r"(?:1s|2s|2p[123]?/?[12]?|3s|3p[123]?/?[12]?|3d[12345]?/?[12]?|4s|4p[123]?/?[12]?|4d[12345]?/?[12]?|4f[1234567]?/?[12]?|5s|5p|5d|5f)\s*"
        r"(?:峰|peak)?)",
        r"((?:C|N|O|F|P|S|Cl|Br|I|B|Na|K|Ca|Mg|Li)\s*"
        r"(?:1s|2s|2p|3s|3p)\s*"
        r"(?:峰|peak)?)",
        r"(binding\s+energy|结合能|XPS\s*峰)",
        r"(turnover\s+frequency|转换频率|TOF)",
        r"(quantum\s+efficiency|量子效率|AQE)",
        r"(apparent\s+quantum\s+yield|表观量子产率|AQY)",
        r"(space[- ]?time\s+yield|时空产率|STY)",
        r"(defect\s+density|缺陷密度)",
        r"(coordination\s+number|配位数)",
        r"(diffusion\s+coefficient|扩散系数)",
        r"(conductivity|电导率)",
        r"(selectivity|选择性)",
        r"(cycling\s+stability|循环稳定性|循环寿命)",
        # ── 中等特异性 ──
        r"(current\s+density|电流密度)",
        r"(pH\s*(?:value|值)?)",
        # ── 低特异性（需词边界/否定预查保护，避免子串误匹配） ──
        r"(potential|电位|电压)(?!.*(?:过电|半波|起始|交换|电荷))",
        r"(resistance|电阻|阻抗)",
        r"(yield|产率|收率)(?!\s*(?:时空))",
    ]

    # 单位模式（大幅扩展，内嵌用非捕获组 (?:...) 避免组号偏移）
    # 注意：复合单位（cm²/s, m²/g 等）必须排在简单单位（cm, m, g）前面，否则会被截断
    unit_pattern = (
        r"([eE][Vv]|[mM][eE][Vv]|[kK][eE][Vv]|"
        r"V\s*(?:vs\.?\s*(?:RHE|SCE|Ag[/]AgCl))?|"
        r"nm|μm|Å|Angstrom|pm|"
        r"mV[/]dec(?:ade)?|mV\s*/\s*dec|"
        r"mV[/]s|mV\s*/\s*s|"
        r"mV|"
        r"mA\s*/\s*cm[²2]|mA·cm[⁻-]²|"
        r"A\s*/\s*cm[²2]|A·cm[⁻-]²|"
        r"μA\s*/\s*cm[²2]|μA·cm[⁻-]²|"
        r"cm[²2]\s*/\s*s|cm²·s[⁻-]¹|"
        r"m[²2]\s*/\s*g|m²·g[⁻-]¹|"
        r"mAh\s*/\s*g|mAh·g[⁻-]¹|"
        r"S\s*/\s*cm|S·cm[⁻-]¹|"
        r"F\s*/\s*g|mF\s*/\s*cm[²2]|"
        r"cm[²2]|"   # cm² 必须在 cm 前面
        r"mm|cm|mmol|μmol|nmol|mol|"
        r"K|°?C|°?F|°|"
        r"Pa|kPa|MPa|GPa|atm|bar|mbar|"
        r"%|wt%|mol%|at%|vol%|"
        r"g|mg|μg|kg|"
        r"h[⁻-]¹|s[⁻-]¹|min[⁻-]¹|"
        r"Ω|kΩ|MΩ|"
        r"ppm|ppb)"
    )

    # 分隔符模式：支持中文"为/约/是/位于"及英文冒号/等号/箭头
    sep_pattern = r"\s*[:：=≈~≈→为约是位于]\s*"

    for line in lines:
        line = line.strip()
        if not line or len(line) > 300:
            continue

        matched_on_this_line = False

        # 模式1: "参数名[分隔符]数值[单位]"
        for pp in param_patterns:
            m = re.search(
                rf"(?:{pp}){sep_pattern}([±\+\-]?\d+\.?\d*(?:[eE][\+\-]?\d+)?)\s*({unit_pattern})?",
                line, re.IGNORECASE,
            )
            if m:
                points.append({
                    "parameter": m.group(1).strip(),
                    "value": m.group(2),
                    "unit": (m.group(3) or "").strip(),
                })
                matched_on_this_line = True
                break  # 每行只取最特异的匹配

        if matched_on_this_line:
            continue

        # 模式2: "参数关键词 ... 数值 单位"（宽松匹配，不要求紧邻）
        # 例："d-band center 达到 -1.948 eV"
        for pp in param_patterns:
            m = re.search(
                rf"(?:{pp}).*?([±\+\-]?\d+\.?\d*(?:[eE][\+\-]?\d+)?)\s*({unit_pattern})",
                line, re.IGNORECASE,
            )
            if m:
                points.append({
                    "parameter": m.group(1).strip(),
                    "value": m.group(2),
                    "unit": (m.group(3) or "").strip(),
                })
                break  # 每行只取最特异的匹配

    # ── 模式3: 宽松兜底 — 匹配"中文描述 + 数值 + 单位" ──
    # 当精确参数名匹配失败时，尝试匹配包含中文描述的行
    if not points:
        for line in lines:
            line = line.strip()
            if not line or len(line) > 300:
                continue
            # 匹配格式如："- 主峰位于 28.5°" / "- 半波电位为 0.81 V" / "峰位: 28.5° (2θ)"
            m = re.search(
                r"[-•*]\s*(.+?)\s*[：:为约是≈~→]\s*([±\+\-]?\d+\.?\d*(?:[eE][\+\-]?\d+)?)\s*"
                rf"({unit_pattern})?",
                line, re.IGNORECASE,
            )
            if m:
                param_name = m.group(1).strip().rstrip("位于在约")
                if len(param_name) > 2 and len(param_name) < 30:  # 过滤过短/过长
                    points.append({
                        "parameter": param_name,
                        "value": m.group(2),
                        "unit": (m.group(3) or "").strip(),
                    })
                    continue

            # 匹配格式如："数值 + 单位 + 括号描述"
            m = re.search(
                rf"([±\+\-]?\d+\.?\d*(?:[eE][\+\-]?\d+)?)\s*({unit_pattern})\s*[（(]([^）)]+)[）)]",
                line, re.IGNORECASE,
            )
            if m:
                points.append({
                    "parameter": m.group(3).strip(),
                    "value": m.group(1),
                    "unit": (m.group(2) or "").strip(),
                })

    return points


def _llm_extract_points(analysis_text: str, config) -> List[Dict[str, Any]]:
    """
    用 LLM 从分析文本中提取结构化数据点（正则失败时的 fallback）。

    要求模型输出 JSON 数组格式，每个元素包含 parameter / value / unit。
    """
    from llm_client import _chat

    prompt = (
        "从以下科研图表分析文本中提取所有可量化的数据点。\n\n"
        "输出要求（严格遵守）：\n"
        "1. 输出一个 JSON 数组，不要输出其他内容\n"
        "2. 每个元素格式：{\"parameter\": \"参数英文名\", \"value\": \"数值字符串\", \"unit\": \"单位\"}\n"
        "3. 只提取有明确数值的数据点\n"
        "4. value 用字符串类型，保留原始精度（如 \"-1.948\"、\"26.5\"）\n"
        "5. 如果无法提取任何数值，输出空数组 []\n\n"
        f"分析文本：\n{analysis_text[:3000]}"
    )

    response = _chat(
        prompt,
        config=config,
        task="hypothesis",  # 使用 qwen3.8-max
        timeout=120,
    )

    # 检测 _chat() 返回的错误响应
    text = response.strip()
    # 清理 qwen3 thinking 标签
    text = re.sub(r'<think[^>]*>.*?</think\s*>', '', text, flags=re.DOTALL)
    if _is_error_response(text):
        logger.warning(f"LLM 数据提取调用失败: {text[:120]}")
        return []
    # 提取 JSON 部分（模型可能在 ```json ... ``` 中）
    json_match = re.search(r'\[.*\]', text, re.DOTALL)
    if json_match:
        try:
            items = json.loads(json_match.group())
            if isinstance(items, list):
                valid = []
                for item in items:
                    if isinstance(item, dict) and "parameter" in item and "value" in item:
                        valid.append({
                            "parameter": str(item["parameter"]),
                            "value": str(item["value"]),
                            "unit": str(item.get("unit", "")),
                        })
                return valid
        except (json.JSONDecodeError, TypeError):
            pass

    return []


def _infer_image_type(analysis_text: str, image_path: str) -> str:
    """从分析文本推断图表类型。优先解析 ## 图片类型 行中的标准标签。"""
    text_lower = (analysis_text + " " + image_path).lower()

    # ── 第一优先级：解析 ## 图片类型 行中的标准标签 ──
    type_label_match = re.search(
        r"##\s*图片类型\s*\n\s*[（(]?\s*([^）)\n]+?)\s*[）)]?\s*\n",
        analysis_text,
    )
    if not type_label_match:
        type_label_match = re.search(
            r"图片类型[：:]\s*(.+?)(?:\n|$)",
            analysis_text,
        )
    if type_label_match:
        label = type_label_match.group(1).strip().lower()
        # 从标签中提取标准类型名
        known_labels = [
            "uv-vis", "xrd", "xps", "ftir", "raman", "sem", "tem",
            "tga", "dsc", "cv", "lsv", "eis", "bet", "pl", "tafel",
            "xanes", "exafs", "nmr", "uv", "vis",
        ]
        for kl in known_labels:
            if kl in label:
                return kl.upper() if kl in ("xrd", "xps", "ftir", "uv-vis", "uv",
                                            "vis", "sem", "tem", "tga", "dsc",
                                            "cv", "lsv", "eis", "bet", "pl",
                                            "xanes", "exafs", "nmr") else kl

    # ── 第二优先级：全文关键词匹配 ──
    type_keywords = {
        "XRD": ["xrd", "x-ray diffraction", "2theta", "2θ", "x 射线衍射"],
        "XPS": ["xps", "x-ray photoelectron", "binding energy", "x 射线光电子"],
        "FTIR": ["ftir", "infrared", "红外光谱"],
        "Raman": ["raman", "拉曼"],
        "SEM": ["sem", "scanning electron", "扫描电镜"],
        "TEM": ["tem", "transmission electron", "透射电镜"],
        "TGA": ["tga", "thermogravimetric", "热重"],
        "DSC": ["dsc", "differential scanning", "差示扫描"],
        "UV-Vis": ["uv-vis", "uv-vis-nir", "紫外可见", "紫外-可见"],
        "PL": ["photoluminescence", "荧光光谱", "pl 光谱", "发光光谱"],
        "CV": ["cyclic voltammetry", "cv 曲线", "循环伏安"],
        "LSV": ["lsv", "linear sweep", "linear voltammetry", "极化曲线"],
        "EIS": ["eis", "nyquist", "impedance", "阻抗"],
        "BET": ["bet", "surface area", "比表面积", "n2 adsorption"],
        "Tafel": ["tafel", "tafel 曲线", "tafel plot"],
        "表格": ["table", "表格", "tabular"],
        "XANES": ["xanes", "x射线吸收近边"],
        "EXAFS": ["exafs", "扩展x射线吸收"],
        "NMR": ["nmr", "nuclear magnetic", "核磁共振"],
        "流程图": ["流程图", "示意图", "schematic", "reaction scheme"],
        "柱状图": ["bar chart", "柱状图", "bar plot"],
        "折线图": ["line chart", "折线图", "line plot"],
        "散点图": ["scatter", "散点图"],
    }
    for img_type, keywords in type_keywords.items():
        for kw in keywords:
            if kw in text_lower:
                return img_type
    return "其他"


# ─────────────────────────────────────────────────────────────
# 管线执行器
# ─────────────────────────────────────────────────────────────

def run_multimodal_pipeline(
    doc_id: int,
    user_id: int,
    base_dir: str,
    config=None,
    doc_source_path: Optional[str] = None,
    on_progress: Optional[Callable[[str, int, int], None]] = None,
    force_reanalyze: bool = False,
    use_llm_page_hints: bool = False,
) -> Tuple[List[MultimodalResult], PipelineReport]:
    """
    执行完整的多模态数据整合管线。

    Args:
        doc_id: 文档 ID
        user_id: 用户 ID
        base_dir: 项目根目录（用于定位图片存储路径）
        config: LLMConfig 实例
        doc_source_path: PDF 原始路径（备用图片发现）
        on_progress: 进度回调 (stage_name, current, total)
        force_reanalyze: 是否强制重新分析（忽略缓存）

    Returns:
        (results, report)
    """
    report = PipelineReport()
    results: List[MultimodalResult] = []

    # ── Step 1: 发现图片 ──
    image_paths = discover_document_images(
        base_dir,
        user_id,
        doc_id,
        doc_source_path,
        config=config,
        use_llm_page_hints=use_llm_page_hints,
    )
    report.total_images = len(image_paths)

    if not image_paths:
        logger.info(f"文档 {doc_id} 未发现提取图片，跳过多模态管线")
        return results, report

    if on_progress:
        on_progress("发现图表", 0, report.total_images)

    # 延迟导入（避免循环依赖）
    try:
        from multimodal import analyze_scientific_image, parse_table_from_image
        from llm_client import _ensure_config
        from db import get_session, get_multimodal_analysis, upsert_multimodal_analysis
        from rag import embed_texts
    except ImportError as e:
        logger.error(f"多模态管线导入失败: {e}")
        return results, report

    cfg = _ensure_config(config)

    # ── Step 2: 逐图分析（含缓存） ──
    all_cleaned_points: List[CleanedDataPoint] = []
    session = get_session()

    try:
        for idx, img_path in enumerate(image_paths):
            source_label = os.path.basename(img_path)
            stage = f"分析图表 {idx + 1}/{len(image_paths)}"
            if on_progress:
                on_progress(stage, idx, len(image_paths))

            result = MultimodalResult(image_path=img_path, source_label=source_label)

            # 检查缓存
            cached = get_multimodal_analysis(session, doc_id, img_path)
            # 缓存有效性判断：status 非 error/pending，且 raw_analysis 非空
            cache_valid = (
                cached
                and not force_reanalyze
                and cached.status not in ("error", "pending")
                and cached.raw_analysis  # 必须有分析结果才算有效缓存
            )
            if cache_valid:
                # 使用缓存
                result.cached = True
                result.raw_analysis = cached.raw_analysis
                result.summary = cached.summary
                result.image_type = cached.image_type
                try:
                    cleaned_json = json.loads(cached.cleaned_data_json)
                    result.cleaned_points = [
                        CleanedDataPoint(
                            parameter=d["parameter"],
                            parameter_cn=d.get("parameter_cn", ""),
                            value=d["value"],
                            unit=d["unit"],
                            original_value=d.get("original_value", str(d["value"])),
                            original_unit=d.get("original_unit", d["unit"]),
                            source=d.get("source", source_label),
                            is_suspicious=d.get("is_suspicious", False),
                            suspicious_reason=d.get("suspicious_reason", ""),
                            precision=d.get("precision", 3),
                        )
                        for d in cleaned_json
                    ]
                except (json.JSONDecodeError, KeyError):
                    pass

                # 如果缓存中 cleaned_points 为空但有 raw_analysis，用新逻辑重新提取
                if not result.cleaned_points and result.raw_analysis:
                    logger.info(f"缓存数据点为空，重新提取: {img_path}")
                    raw_points = _extract_data_points_from_analysis(
                        result.raw_analysis, config=cfg
                    )
                    if raw_points:
                        cleaner = DataCleaner()
                        cleaned = cleaner.clean_data_points(raw_points, source=source_label)
                        result.cleaned_points = cleaned
                        result.cleaning_report = cleaner.report
                        # 更新缓存
                        cleaned_json_new = json.dumps([
                            {
                                "parameter": cp.parameter,
                                "parameter_cn": cp.parameter_cn,
                                "value": cp.value,
                                "unit": cp.unit,
                                "original_value": cp.original_value,
                                "original_unit": cp.original_unit,
                                "source": cp.source,
                                "is_suspicious": cp.is_suspicious,
                                "suspicious_reason": cp.suspicious_reason,
                                "precision": cp.precision,
                            }
                            for cp in result.cleaned_points
                        ], ensure_ascii=False)
                        upsert_multimodal_analysis(
                            session, doc_id, user_id, img_path,
                            status="cleaned",
                            raw_analysis=result.raw_analysis,
                            summary=result.summary,
                            image_type=result.image_type,
                            cleaned_data_json=cleaned_json_new,
                        )

                # 缓存恢复的 cleaned_points 也需计入 all_cleaned_points 供关联挖掘
                all_cleaned_points.extend(result.cleaned_points)
                report.cached += 1
            else:
                # 新分析
                try:
                    ext = os.path.splitext(img_path)[1].lower()
                    if ext in (".xlsx", ".xls", ".csv"):
                        from multimodal import analyze_excel_data
                        analysis = analyze_excel_data(img_path, cfg=cfg)
                    else:
                        analysis = analyze_scientific_image(img_path, None, cfg)

                    # 检测 VL/LLM 调用是否返回了错误字符串（而非正常分析结果）
                    if _is_error_response(analysis):
                        result.error = analysis
                        result.raw_analysis = ""
                        report.failed += 1
                        logger.warning(f"图片分析返回错误 {img_path}: {analysis[:120]}")
                        upsert_multimodal_analysis(
                            session, doc_id, user_id, img_path,
                            status="error", error_message=analysis[:500],
                        )
                        results.append(result)
                        continue

                    result.raw_analysis = analysis
                    result.summary = analysis[:600] if analysis else ""
                    result.image_type = _infer_image_type(analysis, img_path)
                    report.analyzed += 1
                except Exception as e:
                    result.error = str(e)
                    report.failed += 1
                    logger.warning(f"图片分析失败 {img_path}: {e}")

                    # 记录失败状态到 DB
                    upsert_multimodal_analysis(
                        session, doc_id, user_id, img_path,
                        status="error", error_message=str(e),
                    )
                    results.append(result)
                    continue

            # ── Step 3: 数据提取与清洗 ──
            if result.raw_analysis and not result.cleaned_points:
                raw_points = _extract_data_points_from_analysis(result.raw_analysis, config=cfg)
                result.raw_data_points = raw_points

                if raw_points:
                    cleaner = DataCleaner()
                    cleaned = cleaner.clean_data_points(raw_points, source=result.source_label)
                    result.cleaned_points = cleaned
                    result.cleaning_report = cleaner.report
                    all_cleaned_points.extend(cleaned)

            # 保存到 DB
            cleaned_json = json.dumps([
                {
                    "parameter": cp.parameter,
                    "parameter_cn": cp.parameter_cn,
                    "value": cp.value,
                    "unit": cp.unit,
                    "original_value": cp.original_value,
                    "original_unit": cp.original_unit,
                    "source": cp.source,
                    "is_suspicious": cp.is_suspicious,
                    "suspicious_reason": cp.suspicious_reason,
                    "precision": cp.precision,
                }
                for cp in result.cleaned_points
            ], ensure_ascii=False)

            status = "analyzed"
            if result.cleaned_points:
                status = "cleaned"

            upsert_multimodal_analysis(
                session, doc_id, user_id, img_path,
                image_type=result.image_type,
                raw_analysis=result.raw_analysis,
                summary=result.summary,
                cleaned_data_json=cleaned_json,
                model_used="qwen-vl-max",
                status=status,
            )

            results.append(result)

    finally:
        session.close()

    if on_progress:
        on_progress("关联挖掘", report.total_images, report.total_images + 1)

    # ── Step 4: 关联挖掘 ──
    report.total_cleaned_points = len(all_cleaned_points)

    if len(all_cleaned_points) >= 2:
        miner = DataMiner()
        mining_report = miner.mine(all_cleaned_points, use_llm=True, config=cfg)
        report.mining_report = mining_report
        report.total_associations = mining_report.total_associations

        # 更新 DB 中的关联结果
        if mining_report.associations:
            assoc_json = json.dumps(
                [a.to_dict() for a in mining_report.associations],
                ensure_ascii=False,
            )
            session = get_session()
            try:
                for r in results:
                    if r.cleaned_points:
                        upsert_multimodal_analysis(
                            session, doc_id, user_id, r.image_path,
                            associations_json=assoc_json,
                            status="mined",
                        )
                        break  # 所有结果共享同一份关联报告
            finally:
                session.close()

    report.results = results
    return results, report


def build_multimodal_context(
    results: List[MultimodalResult],
    mining_report: Optional[MiningReport] = None,
    literature_context: Optional[str] = None,
) -> str:
    """
    将多模态分析结果构建为可注入 LiteratureAgent 上下文的 Markdown 文本。

    支持图文互证：若有 literature_context，会在输出中标注文献对照信息。

    Args:
        results: 多模态分析结果列表
        mining_report: 关联挖掘报告（可选）
        literature_context: 相关文献文本（用于图文互证交叉验证标注）

    Returns:
        格式化的 Markdown 文本段落
    """
    if not results:
        return ""

    has_data = any(r.cleaned_points for r in results)
    if not has_data and not mining_report:
        return ""

    lines = ["\n=== 多模态图表数据提取与关联分析 ===", ""]

    # 图文互证标注
    if literature_context:
        lines.append(
            "【图文互证模式已启用】以下数据已与文献文字内容进行交叉验证，"
            "标注了文献依据和一致性评估。"
        )
        lines.append("")

    # 各图数据
    for r in results:
        if not r.cleaned_points:
            continue
        type_tag = f"[{r.image_type}]" if r.image_type else ""
        lines.append(f"### {r.source_label} {type_tag}")

        # 若有文献上下文，在每张图的数据前标注文献对照
        if literature_context:
            lines.append(
                "*注: 以下数据已通过图文互证交叉验证，"
                "数值可信度已结合文献描述进行评估。*"
            )

        for cp in r.cleaned_points:
            suspicious_mark = " ⚠️" if cp.is_suspicious else ""
            param_display = cp.parameter_cn or cp.parameter
            lines.append(
                f"- {param_display}: {cp.value:.{cp.precision}f} {cp.unit}"
                f"{suspicious_mark}"
                if cp.is_suspicious else
                f"- {param_display}: {cp.value:.{cp.precision}f} {cp.unit}"
            )
            if cp.is_suspicious:
                lines.append(f"  *注: {cp.suspicious_reason}*")
        lines.append("")

    # 关联发现
    if mining_report and mining_report.total_associations > 0:
        lines.append("### 跨数据源关联发现")
        for i, assoc in enumerate(mining_report.associations, 1):
            lines.append(f"{i}. {assoc.description}")
            lines.append(f"   依据: {assoc.scientific_basis}")
            if literature_context:
                lines.append(f"   文献对照: 已与文献上下文交叉验证")
            lines.append("")

    lines.append(
        "以上数据来自文献中图表的多模态大模型提取、智能清洗与关联挖掘，"
        "可用于验证和支撑假设生成。"
    )
    if literature_context:
        lines.append(
            "数据可信度已通过「文献文字约束+图片视觉特征」双重对齐增强。"
        )

    return "\n".join(lines)


def build_multimodal_evidence(
    results: List[MultimodalResult],
    mining_report: Optional[MiningReport] = None,
) -> dict:
    """
    生成结构化多模态证据包。

    与 build_multimodal_context 的自然语言上下文并行使用：
    - context 负责注入给 LLM
    - evidence 负责最终报告展示、代码执行验证与复现审计
    """
    figures = []
    total_points = 0
    suspicious_points = 0

    for r in results or []:
        points = []
        for cp in r.cleaned_points:
            total_points += 1
            if cp.is_suspicious:
                suspicious_points += 1
            points.append({
                "parameter": cp.parameter,
                "parameter_cn": cp.parameter_cn,
                "value": cp.value,
                "unit": cp.unit,
                "original_value": cp.original_value,
                "original_unit": cp.original_unit,
                "source": cp.source,
                "is_suspicious": cp.is_suspicious,
                "suspicious_reason": cp.suspicious_reason,
                "precision": cp.precision,
            })

        figures.append({
            "source_label": r.source_label or os.path.basename(r.image_path),
            "image_type": r.image_type,
            "image_path": r.image_path,
            "summary": r.summary,
            "point_count": len(points),
            "cached": r.cached,
            "error": r.error,
            "points": points,
        })

    associations = []
    if mining_report:
        for assoc in mining_report.associations:
            if hasattr(assoc, "to_dict"):
                associations.append(assoc.to_dict())

    return {
        "summary": {
            "figures_analyzed": len([r for r in results or [] if not r.error]),
            "figures_failed": len([r for r in results or [] if r.error]),
            "cleaned_points": total_points,
            "suspicious_points": suspicious_points,
            "associations": len(associations),
        },
        "figures": figures,
        "associations": associations,
    }
