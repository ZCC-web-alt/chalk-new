"""
多模态支持模块 — 图片理解 + 表格数据解析。

通过阿里云 DashScope API 调用 Qwen VL 系列多模态模型，
实现对科研论文截图、实验数据图表、Excel 表格的智能解析。

核心函数:
  - analyze_scientific_image()   — 学术图表/截图理解
  - parse_table_from_image()     — 表格图片 OCR → Markdown
  - analyze_excel_data()         — Excel 文件读取 + LLM 解读
  - multimodal_to_context()      — 统一入口，自动检测文件类型
"""

import base64
import os
from typing import Dict, List, Optional, Union

import pandas as pd
import requests

from llm_client import (
    LLMConfig,
    DASHSCOPE_BASE_URL,
    _chat,
    _ensure_config,
    _get_api_key,
)


# ─────────────────────────────────────────────────────────────
# 多模态模型路由
# ─────────────────────────────────────────────────────────────

MULTIMODAL_MODELS = {
    "image_understand": "qwen-vl-max",   # 学术图表理解（最强推理）
    "table_ocr":      "qwen-vl-ocr",     # 表格 OCR（专用模型）
    "chart_analysis": "qwen-vl-max",     # 数据图表分析
    "excel_interpret":"qwen3.7-max",       # Excel 数据解读（纯文本推理）
}


def _encode_image_base64(image_path: str) -> str:
    """读取图片文件，编码为 base64 data URI。"""
    ext = os.path.splitext(image_path)[1].lower()
    mime_map = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png",  ".gif": "image/gif",
        ".bmp": "image/bmp",  ".webp": "image/webp",
        ".tiff": "image/tiff", ".tif": "image/tiff",
    }
    mime = mime_map.get(ext, "image/jpeg")

    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime};base64,{b64}"


# ─────────────────────────────────────────────────────────────
# 底层多模态调用
# ─────────────────────────────────────────────────────────────

def _chat_multimodal(
    prompt: str,
    images: List[Union[str, bytes]],
    config: LLMConfig,
    task: str = "image_understand",
    system_prompt: Optional[str] = None,
    timeout: int = 180,
    retries: int = 2,
) -> str:
    """
    多模态对话：发送文本 + 图片到 Qwen VL 模型。

    Args:
        prompt: 文本提示词
        images: 图片列表 — 文件路径(str) 或 data URI 或 原始字节(bytes)
        config: LLMConfig 实例
        task: 模型路由键名 (image_understand / table_ocr / chart_analysis)
        system_prompt: 系统提示词（可选）
        timeout: 读取超时秒数（默认 180 秒，VL 模型响应较慢）
        retries: 超时/网络错误时的自动重试次数（默认 2 次）

    Returns:
        模型返回的文本内容
    """
    import time

    api_key = _get_api_key(config)
    model = MULTIMODAL_MODELS.get(task, "qwen-vl-max")
    base_url = config.base_url or DASHSCOPE_BASE_URL
    url = f"{base_url.rstrip('/')}/chat/completions"

    # 构造 content 数组
    content_parts: List[Dict] = []

    # 图片部分
    for img in images:
        if isinstance(img, bytes):
            b64 = base64.b64encode(img).decode("utf-8")
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            })
        elif isinstance(img, str) and img.startswith("data:"):
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": img},
            })
        elif isinstance(img, str) and img.startswith("http"):
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": img},
            })
        elif isinstance(img, str):
            # 本地文件路径
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": _encode_image_base64(img)},
            })

    # 文本部分
    content_parts.append({"type": "text", "text": prompt})

    sys_msg = system_prompt or (
        "你是一名精通化学、材料科学与数据分析的学术研究助手，"
        "擅长解读科研图表、光谱数据和实验表格。"
        "回答时要求严谨准确，对不确定的数值明确标注。"
    )

    # 分离连接超时和读取超时：(connect_timeout, read_timeout)
    timeout_tuple = (15, timeout)

    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": sys_msg},
                        {"role": "user", "content": content_parts},
                    ],
                },
                timeout=timeout_tuple,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except ValueError:
            raise
        except Exception as e:
            last_err = e
            is_timeout = "timed out" in str(e).lower()
            if attempt < retries and is_timeout:
                wait = 5 * (attempt + 1)
                time.sleep(wait)
                continue
            return f"调用多模态接口失败（模型: {model}）：{e}"


# ─────────────────────────────────────────────────────────────
# 学术图片理解
# ─────────────────────────────────────────────────────────────

def analyze_scientific_image(
    image_path: str,
    user_question: Optional[str] = None,
    config: Optional[LLMConfig] = None,
    literature_context: Optional[str] = None,
) -> str:
    """
    分析科研论文截图或实验图表。

    支持的图片类型：
    - XRD / XPS / FTIR / UV-Vis / Raman 光谱图
    - SEM / TEM 形貌照片
    - TGA / DSC 热分析曲线
    - 电化学曲线 (CV / LSV / EIS / Tafel)
    - 反应流程图 / 装置示意图
    - 数据散点图 / 柱状图 / 折线图

    Args:
        image_path: 图片文件路径
        user_question: 用户的额外问题（可选）
        config: LLMConfig 实例
        literature_context: 相关文献文本上下文（用于图文互证交叉验证）

    Returns:
        结构化分析文本（可作为上下文注入主模型）
    """
    cfg = _ensure_config(config)
    question = user_question or "请详细分析这张科研图片"

    # 构建图文互证指令
    cross_validation_inst = ""
    if literature_context:
        cross_validation_inst = (
            "\n\n【图文互证交叉验证（极其重要）】：\n"
            "以下是与该图片相关的文献文字内容（包括原始图注、实验流程描述、变量定义、预期趋势等）。"
            "你必须结合文献文字与图片视觉特征进行交叉验证：\n"
            "1. 对比图片中的视觉特征（曲线走势、峰位、形态、异常等）与文献描述是否一致\n"
            "2. 利用文献中的理论框架解释图片中的隐含信息\n"
            "3. 若图片为XRD图谱，调用文献中关于晶面指数与物相判定的标准进行比对\n"
            "4. 若图片为电化学曲线，结合文献中的反应机理和动力学参数进行解读\n"
            "5. 若发现图片数据与文献描述存在矛盾，必须明确指出并分析可能原因\n"
            "6. 在'科学解读'部分，必须标注'文献依据: ...'，说明解读的文献出处\n\n"
            f"【相关文献上下文】:\n{literature_context[:3000]}\n"
        )

    prompt = (
        "请仔细观察这张科研相关图片，按以下结构提取信息：\n\n"
        "## 图片类型\n"
        "（从以下标准类型中选择最匹配的一个，格式为「类型: XXX」：\n"
        "XRD、XPS、FTIR、UV-Vis、Raman、SEM、TEM、TGA、DSC、\n"
        "CV、LSV、EIS、Tafel、BET、PL、XANES、EXAFS、NMR、\n"
        "表格、流程图/示意图、柱状图、折线图、散点图、其他。\n"
        "如果图片包含多个子图，列出所有子图类型。）\n\n"
        "## 图注与标注\n"
        "（图片中出现的标题、图注文字、坐标轴标签及单位、图例等）\n\n"
        "## 关键数据点\n"
        "【重要】请逐点仔细读取图表中的数值数据：\n"
        "- 对于曲线图（XRD/XPS/FTIR/UV-Vis/Raman/CV/LSV 等）：列出每个特征峰的\n"
        "  位置（角度/能量/电位）、强度值（counts/a.u./mA·cm⁻² 等），\n"
        "  以及半峰宽（FWHM）等可读取参数\n"
        "- 对于柱状图/散点图：列出每个柱/点的坐标值\n"
        "- 对于表格：列出所有行列数据\n"
        "- 对不确定的数值标注 [约] 或 [~] 前缀\n"
        "- 不要遗漏任何可识别的数据点，宁可多列也不要少列\n\n"
        "## 数据趋势分析\n"
        "（描述曲线走向、变化规律、拐点、平台区、异常波动等）\n\n"
        "## 科学解读\n"
        "（基于图片内容，结合化学/材料学专业知识给出分析：可能对应什么物理化学过程、"
        "性能优劣判断、与文献常见值的对比等。\n"
        "若提供了文献上下文，必须结合文献进行交叉验证，并标注'文献依据: ...'）\n\n"
        "## 图文一致性评估\n"
        "（若有文献上下文：评估图片数据与文献描述的一致性，标注一致/部分一致/不一致，"
        "并说明具体差异。若无文献上下文，标注'无文献对照'）\n\n"
        "注意事项：\n"
        "1. 如果某项无法从图片中确定，请明确说明「无法识别」，不要编造数值\n"
        "2. 数值提取要尽量精确，如标注了刻度则据此读数\n"
        "3. 如果图片模糊或信息不足，如实说明\n"
        "4. 不要敷衍了事——务必认真提取每一处可识别的数据\n"
        f"{cross_validation_inst}\n"
        f"【用户问题】：{question}\n"
    )
    return _chat_multimodal(
        prompt, [image_path], cfg,
        task="image_understand", timeout=180,
    )


# ─────────────────────────────────────────────────────────────
# 表格图片 OCR
# ─────────────────────────────────────────────────────────────

def parse_table_from_image(
    image_path: str,
    config: Optional[LLMConfig] = None,
) -> str:
    """
    从表格图片中 OCR 提取结构化数据。

    使用 qwen-vl-ocr 专用模型获得最佳表格识别效果。

    Args:
        image_path: 表格图片文件路径
        config: LLMConfig 实例

    Returns:
        包含 Markdown 表格 + 数据摘要的结构化文本
    """
    cfg = _ensure_config(config)

    prompt = (
        "请仔细识别图片中的表格数据，严格按以下格式输出：\n\n"
        "## 表格结构\n"
        "（总行数、总列数、表头内容）\n\n"
        "## 完整表格（Markdown 格式）\n"
        "| 列1 | 列2 | 列3 |\n"
        "|---|---|---|\n"
        "| 值 | 值 | 值 |\n\n"
        "要求：\n"
        "- 每个单元格的值必须忠实于图片内容\n"
        "- 无法确定的单元格用 ??? 标记\n"
        "- 保持原始表格的行列对应关系\n"
        "- 如果有合并单元格，在注释中说明\n\n"
        "## 数据说明\n"
        "（单位、表注、脚注、特殊符号说明等）\n\n"
        "## 关键数据摘要\n"
        "- 各数值列的范围（最小值 ~ 最大值）\n"
        "- 明显的极值点\n"
        "- 数据中的特殊趋势或规律\n"
    )
    return _chat_multimodal(
        prompt, [image_path], cfg,
        task="table_ocr", timeout=180,
    )


# ─────────────────────────────────────────────────────────────
# Excel 文件解析
# ─────────────────────────────────────────────────────────────

def _df_to_text(df: pd.DataFrame, max_rows: int = 100) -> str:
    """将 DataFrame 转为文本表格（不依赖 tabulate）。"""
    cols = [str(c) for c in df.columns]
    header = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join(["---"] * len(cols)) + " |"
    lines = [header, sep]
    for _, row in df.head(max_rows).iterrows():
        vals = [str(v) if pd.notna(v) else "" for v in row]
        lines.append("| " + " | ".join(vals) + " |")
    if len(df) > max_rows:
        lines.append(f"\n（共 {len(df)} 行，仅显示前 {max_rows} 行）")
    return "\n".join(lines)


def analyze_excel_data(
    file_path: str,
    sheet_name: Optional[str] = None,
    user_question: Optional[str] = None,
    config: Optional[LLMConfig] = None,
) -> str:
    """
    读取 Excel 文件，用 LLM 进行数据解读。

    工作流程：
    1. pandas + openpyxl 读取 Excel
    2. 转换为 Markdown 表格文本
    3. 发送给 LLM 进行专业分析

    Args:
        file_path: Excel 文件路径 (.xlsx / .xls)
        sheet_name: 指定工作表名（可选，默认读取前 3 个）
        user_question: 用户额外问题（可选）
        config: LLMConfig 实例

    Returns:
        数据分析结果文本
    """
    cfg = _ensure_config(config)
    question = user_question or "请分析这组实验数据"

    try:
        xls = pd.ExcelFile(file_path)
        sheets = xls.sheet_names

        if sheet_name and sheet_name in sheets:
            sheets_to_read = [sheet_name]
        else:
            sheets_to_read = sheets[:3]  # 最多 3 个 sheet

        data_parts = []
        for sname in sheets_to_read:
            df = pd.read_excel(xls, sheet_name=sname)
            # 基本统计
            numeric_cols = df.select_dtypes(include="number").columns.tolist()
            stats = ""
            if numeric_cols:
                desc = df[numeric_cols].describe().to_string()
                stats = f"\n数值统计:\n```\n{desc}\n```"

            table_text = _df_to_text(df)
            data_parts.append(
                f"### Sheet: {sname}\n"
                f"形状: {df.shape[0]} 行 × {df.shape[1]} 列\n"
                f"列名: {list(df.columns)}\n"
                f"数据类型: {dict(df.dtypes.apply(str))}\n"
                f"{stats}\n\n"
                f"表格数据:\n{table_text}"
            )

        data_text = "\n\n---\n\n".join(data_parts)

        # 发送给文本模型进行解读
        prompt = (
            "下面是从 Excel 文件读取的实验数据（Markdown 表格格式）。"
            "请进行以下分析：\n\n"
            "## 数据概览\n"
            "（数据规模、各列含义、数据类型、缺失情况）\n\n"
            "## 统计摘要\n"
            "（各数值列的均值、范围、标准差，重点关注极值和异常值）\n\n"
            "## 数据趋势与相关性\n"
            "（列与列之间的相关关系、变化趋势、可能的物理化学含义）\n\n"
            "## 科学解读\n"
            "（结合化学/材料学专业知识，分析数据背后的科学意义："
            "性能优劣、反应规律、构效关系等）\n\n"
            "## 建议\n"
            "（基于数据分析给出实验改进或后续实验的建议）\n\n"
            f"【用户问题】：{question}\n\n"
            f"【表格数据】:\n{data_text[:15000]}\n\n"
            "注意：只基于给定的数据进行分析，不要编造数据点。"
        )
        return _chat(
            prompt, cfg, task="qa",
            system_prompt="你是一名精通化学与材料科学实验数据分析的专家。",
            timeout=120,
        )

    except Exception as e:
        return (
            f"读取 Excel 文件失败：{e}\n"
            f"请确认文件格式正确（.xlsx / .xls）且未被其他程序占用。"
        )


# ─────────────────────────────────────────────────────────────
# 统一入口
# ─────────────────────────────────────────────────────────────

def multimodal_to_context(
    file_path: str,
    file_type: str = "auto",
    user_question: Optional[str] = None,
    config: Optional[LLMConfig] = None,
) -> Dict:
    """
    统一入口：自动检测文件类型，调用对应解析函数。

    Args:
        file_path: 文件路径
        file_type: "image" / "excel" / "auto"（根据扩展名自动判断）
        user_question: 用户额外问题（可选）
        config: LLMConfig 实例

    Returns:
        {
            "type": "image" | "excel" | "unknown",
            "source": file_path,
            "analysis": "完整分析文本",
            "summary": "简要摘要（可注入 RAG 上下文）",
        }
    """
    cfg = _ensure_config(config)

    # 自动检测类型
    if file_type == "auto":
        ext = os.path.splitext(file_path)[1].lower()
        if ext in (".xlsx", ".xls", ".csv"):
            file_type = "excel"
        elif ext in (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tiff", ".tif"):
            file_type = "image"
        else:
            return {
                "type": "unknown",
                "source": file_path,
                "analysis": f"不支持的文件格式: {ext}",
                "summary": "",
            }

    if file_type == "image":
        analysis = analyze_scientific_image(file_path, user_question, cfg)
        return {
            "type": "image",
            "source": file_path,
            "analysis": analysis,
            "summary": _extract_summary(analysis),
        }

    elif file_type == "excel":
        analysis = analyze_excel_data(file_path, user_question=user_question, cfg=cfg)
        return {
            "type": "excel",
            "source": file_path,
            "analysis": analysis,
            "summary": _extract_summary(analysis),
        }

    return {
        "type": "unknown",
        "source": file_path,
        "analysis": "未知文件类型",
        "summary": "",
    }


def _extract_summary(analysis: str, max_len: int = 600) -> str:
    """从分析结果中提取简要摘要，用于注入 RAG 上下文。"""
    if not analysis or "失败" in analysis[:30]:
        return ""
    lines = analysis.strip().splitlines()
    # 取前 30 行非空行
    non_empty = [l.strip() for l in lines if l.strip()]
    summary = "\n".join(non_empty[:30])
    if len(summary) > max_len:
        summary = summary[:max_len] + "\n...(内容已截断)"
    return summary
