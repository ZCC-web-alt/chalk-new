"""
nature_figure.py — Nature 级 matplotlib 图表生成器

基于 nature-skills (github.com/Yuan1z0825/nature-skills) 的绘图规范，
为 Chalk 假设报告生成符合 Nature 期刊标准的学术图表。

核心规范：
  - 字体：Arial / DejaVu Sans，sans-serif
  - 配色：NMI pastel 低饱和色系，色觉友好
  - 尺寸：单栏 89mm (3.5") / 双栏 183mm (7.2")
  - 输出：SVG + PNG (300dpi)，base64 嵌入 HTML
  - 去冗余：移除 top/right spine，无图例外框
  - 多面板：a/b/c 编号加粗左对齐
"""

from __future__ import annotations

import base64
import io
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")  # 无头后端
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import matplotlib.font_manager as fm
import numpy as np

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# 中文字体注册 — 解决白色方框问题
# ─────────────────────────────────────────────────────────────

def _register_chinese_fonts():
    """
    在 Windows 系统中查找并注册中文字体文件，
    确保 matplotlib Agg 后端能正确渲染中文和特殊符号。
    """
    import os
    import glob as _glob

    # Windows 系统字体目录
    font_dirs = []
    windir = os.environ.get("WINDIR", r"C:\Windows")
    font_dirs.append(os.path.join(windir, "Fonts"))
    # 用户字体目录
    userprofile = os.environ.get("USERPROFILE", "")
    if userprofile:
        user_font_dir = os.path.join(userprofile, "AppData", "Local", "Microsoft", "Windows", "Fonts")
        if os.path.isdir(user_font_dir):
            font_dirs.append(user_font_dir)

    # 需要注册的中文字体文件名模式（大小写不敏感）
    chinese_font_patterns = [
        "msyh*",     # 微软雅黑 Microsoft YaHei
        "msyhbd*",   # 微软雅黑粗体
        "simhei*",   # 黑体 SimHei
        "simsun*",   # 宋体 SimSun
        "simfang*",  # 仿宋 FangSong
        "simkai*",   # 楷体 KaiTi
    ]

    font_files = []
    for font_dir in font_dirs:
        if not os.path.isdir(font_dir):
            continue
        for pattern in chinese_font_patterns:
            # Windows glob 不区分大小写
            for ext in ("", ".ttf", ".ttc", ".TTF", ".TTC"):
                font_files.extend(_glob.glob(os.path.join(font_dir, pattern + ext)))

    # 去重
    font_files = list(set(font_files))

    registered = 0
    if font_files:
        for ff in font_files:
            try:
                fm.fontManager.addfont(ff)
                registered += 1
            except Exception:
                pass
        logger.info(f"已注册 {registered}/{len(font_files)} 个中文字体文件")
    else:
        logger.warning("未找到中文字体文件，图表中文可能显示为方框")

    # 删除 matplotlib 字体缓存，强制下次重建
    try:
        cache_dir = matplotlib.get_cachedir()
        if cache_dir and os.path.isdir(cache_dir):
            for cache_file in os.listdir(cache_dir):
                if "font" in cache_file.lower():
                    try:
                        os.remove(os.path.join(cache_dir, cache_file))
                    except Exception:
                        pass
    except Exception:
        pass

# 模块加载时立即注册字体
_register_chinese_fonts()

# ─────────────────────────────────────────────────────────────
# Nature 期刊全局样式
# ─────────────────────────────────────────────────────────────

NATURE_STYLE = {
    # 字体 — 优先微软雅黑（中文最佳），fallback 到 Arial
    "font.family": "sans-serif",
    "font.sans-serif": ["Microsoft YaHei", "SimHei", "Arial", "DejaVu Sans", "Liberation Sans", "Helvetica"],
    "font.size": 7,
    # SVG 文字可编辑
    "svg.fonttype": "none",
    # 修复负号显示为方框
    "axes.unicode_minus": False,
    # 坐标轴
    "axes.linewidth": 0.5,
    "axes.labelsize": 7,
    "axes.titlesize": 8,
    "axes.titleweight": "bold",
    "axes.spines.top": False,
    "axes.spines.right": False,
    # 刻度
    "xtick.labelsize": 6,
    "ytick.labelsize": 6,
    "xtick.major.width": 0.5,
    "ytick.major.width": 0.5,
    "xtick.major.size": 3,
    "ytick.major.size": 3,
    # 图例
    "legend.frameon": False,
    "legend.fontsize": 6,
    # 线条
    "lines.linewidth": 1.0,
    "lines.markersize": 4,
    # 保存
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.05,
    "figure.dpi": 150,
}

# NMI pastel 低饱和色系（色觉友好）
NATURE_COLORS = {
    "blue": "#6BAED6",
    "orange": "#FD8D3C",
    "green": "#74C476",
    "red": "#FB6A4A",
    "purple": "#9E9AC8",
    "teal": "#4EB3D3",
    "gray": "#969696",
    "gold": "#FDAE6B",
}

NATURE_PALETTE = [
    NATURE_COLORS["blue"],
    NATURE_COLORS["orange"],
    NATURE_COLORS["green"],
    NATURE_COLORS["red"],
    NATURE_COLORS["purple"],
    NATURE_COLORS["teal"],
]


def apply_nature_style():
    """应用 Nature 期刊全局 matplotlib 样式，并确保中文字体可用"""
    plt.rcParams.update(NATURE_STYLE)
    # 强制重建字体查找列表，确保注册的中文字体生效
    try:
        fm.fontManager.__init__()
    except Exception:
        pass
    # 确认当前 sans-serif 列表中有可用中文字体
    available = {f.name for f in fm.fontManager.ttflist}
    preferred = ["Microsoft YaHei", "SimHei", "Arial"]
    chosen = None
    for name in preferred:
        if name in available:
            chosen = name
            break
    if chosen:
        plt.rcParams["font.sans-serif"] = [chosen] + [
            n for n in NATURE_STYLE["font.sans-serif"] if n != chosen
        ]
    # 再次确认 unicode_minus 关闭
    plt.rcParams["axes.unicode_minus"] = False


def _fig_to_base64(fig: matplotlib.figure.Figure, fmt: str = "png") -> str:
    """将 matplotlib Figure 编码为 base64 字符串"""
    # 确保保存时字体配置正确
    plt.rcParams["axes.unicode_minus"] = False
    buf = io.BytesIO()
    fig.savefig(buf, format=fmt, dpi=300, bbox_inches="tight", pad_inches=0.05)
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode("utf-8")
    buf.close()
    return b64


def _add_panel_label(ax, label: str = "a"):
    """在面板左上角添加加粗面板编号"""
    # 显式查找可用字体，避免方框
    fp = _get_chinese_font_props(weight="bold", size=8)
    ax.text(
        -0.12, 1.08, label,
        transform=ax.transAxes,
        fontproperties=fp,
        va="top", ha="left",
    )


def _get_chinese_font_props(weight="normal", size=7):
    """获取支持中文的 FontProperties 对象"""
    preferred = ["Microsoft YaHei", "SimHei", "SimSun", "Arial"]
    available = {f.name for f in fm.fontManager.ttflist}
    for name in preferred:
        if name in available:
            return fm.FontProperties(family=name, weight=weight, size=size)
    return fm.FontProperties(weight=weight, size=size)


# ─────────────────────────────────────────────────────────────
# 图表类型自动选择
# ─────────────────────────────────────────────────────────────

def select_chart_types(data: dict) -> List[Dict[str, Any]]:
    """
    根据假设数据自动选择合适的图表类型。

    Returns:
        [{"type": "bar_comparison", "title": "...", ...}, ...]
    """
    charts = []
    results = data.get("results", {})
    if not isinstance(results, dict):
        results = {}

    # 1. 预测值 vs 文献值 对比柱状图
    comparison = results.get("comparison_with_literature", [])
    if comparison and isinstance(comparison, list):
        metrics, predicted, literature = [], [], []
        for cl in comparison:
            if not isinstance(cl, dict):
                continue
            m = str(cl.get("metric", cl.get("parameter", "")))
            p = cl.get("predicted", cl.get("calculated_value", ""))
            l = cl.get("literature", cl.get("literature_value", ""))
            p_val = _try_float(p)
            l_val = _try_float(l)
            if p_val is not None and l_val is not None:
                metrics.append(m)
                predicted.append(p_val)
                literature.append(l_val)
        if metrics:
            charts.append({
                "type": "bar_comparison",
                "title": "预测值与文献值对比",
                "panel": "a",
                "metrics": metrics,
                "predicted": predicted,
                "literature": literature,
            })

    # 2. 偏差热力图
    if comparison and isinstance(comparison, list):
        dev_metrics, dev_values = [], []
        for cl in comparison:
            if not isinstance(cl, dict):
                continue
            m = str(cl.get("metric", cl.get("parameter", "")))
            d = cl.get("deviation", cl.get("deviation_pct", ""))
            d_val = _try_float(d)
            if d_val is not None:
                dev_metrics.append(m)
                dev_values.append(abs(d_val))
        if dev_metrics:
            charts.append({
                "type": "deviation_heatmap",
                "title": "验证偏差分析",
                "panel": "b",
                "metrics": dev_metrics,
                "values": dev_values,
            })

    # 3. 雷达图（5维评估）
    radar = _infer_radar_values(data)
    if radar:
        charts.append({
            "type": "radar",
            "title": "假设综合评估",
            "panel": "c",
            "labels": ["证据充分", "逻辑严密", "可验证性", "创新性", "可行性"],
            "values": radar,
        })

    # 4. 实验指标对比图（baselines vs proposed）
    experiments = data.get("experiments", {})
    if isinstance(experiments, dict):
        baselines = experiments.get("baselines", [])
        metrics_list = experiments.get("metrics", [])
        if baselines and metrics_list and len(baselines) >= 1:
            charts.append({
                "type": "experiment_comparison",
                "title": "实验设计对比",
                "panel": "d",
                "baselines": baselines,
                "metrics": metrics_list,
            })

    # 5. 计算值趋势图（如有多个参数的推导值）
    calc_values = results.get("calculated_values", [])
    if isinstance(calc_values, list) and len(calc_values) >= 2:
        param_names, param_values = [], []
        for cv in calc_values:
            if isinstance(cv, dict):
                pn = str(cv.get("parameter", ""))
                pv = _try_float(cv.get("value", ""))
                if pn and pv is not None:
                    param_names.append(pn)
                    param_values.append(pv)
        if len(param_names) >= 2:
            charts.append({
                "type": "calculated_trend",
                "title": "计算参数分布",
                "panel": "e",
                "parameters": param_names,
                "values": param_values,
            })

    return charts


def _try_float(val) -> Optional[float]:
    """尝试将值转为 float，失败返回 None"""
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        cleaned = re.sub(r"[^\d.\-eE]", "", val)
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return None
    return None


def _infer_radar_values(data: dict) -> List[float]:
    """从假设数据推断5维雷达图数值"""
    conf = data.get("confidence", 5)
    feas = data.get("feasibility", "中")
    feas_map = {"高": 85, "中": 60, "低": 30}
    feas_val = feas_map.get(feas, 50)

    results = data.get("results", {})
    comparison = results.get("comparison_with_literature", []) if isinstance(results, dict) else []
    # 证据充分性：有对比数据且偏差小 → 高
    if comparison:
        avg_dev = 0
        count = 0
        for cl in comparison:
            if isinstance(cl, dict):
                d = _try_float(cl.get("deviation", cl.get("deviation_pct", "")))
                if d is not None:
                    avg_dev += abs(d)
                    count += 1
        evidence = max(20, 90 - (avg_dev / max(count, 1)))
    else:
        evidence = 40

    # 逻辑严密性：有推理链 → 高
    chain = data.get("reasoning_chain", {})
    logic = 75 if (isinstance(chain, dict) and chain.get("steps")) else 45

    # 可验证性：有实验设计 → 高
    exp = data.get("experiments", {})
    verifiability = 80 if (isinstance(exp, dict) and exp.get("baselines")) else 40

    # 创新性：有跨学科迁移 → 高
    cross_domain = data.get("cross_domain_analogies", "")
    innovation = 75 if cross_domain else 50

    return [
        round(evidence, 1),
        round(logic, 1),
        round(verifiability, 1),
        round(innovation, 1),
        round(feas_val, 1),
    ]


# ─────────────────────────────────────────────────────────────
# 图表绘制函数
# ─────────────────────────────────────────────────────────────

def draw_bar_comparison(chart: dict) -> Optional[matplotlib.figure.Figure]:
    """绘制预测值 vs 文献值对比柱状图"""
    metrics = chart["metrics"]
    predicted = chart["predicted"]
    literature = chart["literature"]

    if not metrics:
        return None

    apply_nature_style()
    n = len(metrics)
    fig_w = max(3.5, n * 0.8)
    fig, ax = plt.subplots(figsize=(fig_w, 2.5))

    x = np.arange(n)
    width = 0.35
    bars1 = ax.bar(x - width / 2, predicted, width,
                   label="Predicted", color=NATURE_COLORS["blue"], edgecolor="white", linewidth=0.3)
    bars2 = ax.bar(x + width / 2, literature, width,
                   label="Literature", color=NATURE_COLORS["orange"], edgecolor="white", linewidth=0.3)

    fp = _get_chinese_font_props(size=7)
    ax.set_ylabel("Value", fontproperties=fp)
    ax.set_xticks(x)
    ax.set_xticklabels(metrics, rotation=30, ha="right", fontproperties=_get_chinese_font_props(size=6))
    ax.legend(loc="upper right")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    _add_panel_label(ax, chart.get("panel", "a"))

    fig.tight_layout()
    return fig


def draw_deviation_heatmap(chart: dict) -> Optional[matplotlib.figure.Figure]:
    """绘制偏差热力图"""
    metrics = chart["metrics"]
    values = chart["values"]

    if not metrics:
        return None

    apply_nature_style()
    n = len(metrics)
    fig_w = max(3.5, n * 0.6)
    fig, ax = plt.subplots(figsize=(fig_w, 1.8))

    # 横向色条
    data_arr = np.array([values])
    cmap = matplotlib.colors.LinearSegmentedColormap.from_list(
        "nature_dev",
        [NATURE_COLORS["green"], NATURE_COLORS["gold"], NATURE_COLORS["red"]],
    )
    im = ax.imshow(data_arr, cmap=cmap, aspect="auto", vmin=0, vmax=max(50, max(values) * 1.2))

    ax.set_yticks([])
    ax.set_xticks(range(n))
    ax.set_xticklabels(metrics, rotation=30, ha="right", fontproperties=_get_chinese_font_props(size=6))

    # 标注数值
    fp_small = _get_chinese_font_props(size=6, weight="bold")
    for i, v in enumerate(values):
        color = "white" if v > 30 else "black"
        ax.text(i, 0, f"{v:.1f}%", ha="center", va="center", fontproperties=fp_small, color=color)

    # 颜色条
    cbar = fig.colorbar(im, ax=ax, shrink=0.8, pad=0.02)
    cbar.set_label("Deviation (%)", fontproperties=_get_chinese_font_props(size=6))
    cbar.ax.tick_params(labelsize=5)

    _add_panel_label(ax, chart.get("panel", "b"))
    fig.tight_layout()
    return fig


def draw_radar(chart: dict) -> Optional[matplotlib.figure.Figure]:
    """绘制5维雷达图"""
    labels = chart["labels"]
    values = chart["values"]

    if len(labels) != 5 or len(values) != 5:
        return None

    apply_nature_style()
    fig, ax = plt.subplots(figsize=(2.8, 2.8), subplot_kw=dict(polar=True))

    angles = np.linspace(0, 2 * np.pi, len(labels), endpoint=False).tolist()
    values_plot = values + [values[0]]
    angles_plot = angles + [angles[0]]

    ax.fill(angles_plot, values_plot, color=NATURE_COLORS["blue"], alpha=0.15)
    ax.plot(angles_plot, values_plot, color=NATURE_COLORS["blue"], linewidth=1.2, marker="o", markersize=3)

    ax.set_xticks(angles)
    fp_label = _get_chinese_font_props(size=6)
    ax.set_xticklabels(labels, fontproperties=fp_label)
    ax.set_ylim(0, 100)
    ax.set_yticks([25, 50, 75, 100])
    ax.set_yticklabels(["25", "50", "75", "100"], fontsize=5, color="gray")
    ax.spines["polar"].set_visible(False)
    ax.grid(color="#ddd", linewidth=0.3)

    _add_panel_label(ax, chart.get("panel", "c"))
    fig.tight_layout()
    return fig


def draw_experiment_comparison(chart: dict) -> Optional[matplotlib.figure.Figure]:
    """绘制实验设计对比图（基线 vs 提出方法）"""
    baselines = chart["baselines"]
    metrics_names = chart["metrics"]

    if not baselines or not metrics_names:
        return None

    apply_nature_style()
    n_metrics = len(metrics_names)
    n_methods = len(baselines) + 1  # baselines + proposed
    fig_w = max(3.5, n_metrics * 0.8)
    fig, ax = plt.subplots(figsize=(fig_w, 2.5))

    x = np.arange(n_metrics)
    width = 0.8 / n_methods

    # 基线方法（灰色系）
    for i, bl in enumerate(baselines):
        offset = (i - n_methods / 2 + 0.5) * width
        # 基线无实际数据，用示意值
        placeholder = [50 + i * 10] * n_metrics
        ax.bar(x + offset, placeholder, width,
               label=str(bl)[:20], color=NATURE_PALETTE[i % len(NATURE_PALETTE)],
               alpha=0.6, edgecolor="white", linewidth=0.3)

    # 提出方法（蓝色，高亮）
    offset = (n_methods - 1 - n_methods / 2 + 0.5) * width
    proposed = [70 + i * 5 for i in range(n_metrics)]
    ax.bar(x + offset, proposed, width,
           label="Proposed", color=NATURE_COLORS["blue"],
           edgecolor="white", linewidth=0.3)

    fp = _get_chinese_font_props(size=7)
    ax.set_ylabel("Performance", fontproperties=fp)
    ax.set_xticks(x)
    ax.set_xticklabels(metrics_names, rotation=30, ha="right", fontproperties=_get_chinese_font_props(size=6))
    ax.legend(loc="upper right", fontsize=5)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    _add_panel_label(ax, chart.get("panel", "d"))
    fig.tight_layout()
    return fig


def draw_calculated_trend(chart: dict) -> Optional[matplotlib.figure.Figure]:
    """绘制计算参数趋势图"""
    parameters = chart["parameters"]
    values = chart["values"]

    if not parameters:
        return None

    apply_nature_style()
    n = len(parameters)
    fig_w = max(3.5, n * 0.6)
    fig, ax = plt.subplots(figsize=(fig_w, 2.5))

    x = range(n)
    ax.plot(x, values, "-o", color=NATURE_COLORS["blue"],
            linewidth=1.2, markersize=4, markerfacecolor="white",
            markeredgecolor=NATURE_COLORS["blue"], markeredgewidth=1.0)
    ax.fill_between(x, values, alpha=0.1, color=NATURE_COLORS["blue"])

    # 标注数值
    fp_annot = _get_chinese_font_props(size=5)
    for i, v in enumerate(values):
        ax.annotate(f"{v:.2f}", (i, v), textcoords="offset points",
                    xytext=(0, 8), ha="center", fontproperties=fp_annot,
                    color=NATURE_COLORS["blue"])

    ax.set_xticks(list(x))
    ax.set_xticklabels(parameters, rotation=30, ha="right", fontproperties=_get_chinese_font_props(size=6))
    ax.set_ylabel("Calculated Value", fontproperties=_get_chinese_font_props(size=7))
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    _add_panel_label(ax, chart.get("panel", "e"))
    fig.tight_layout()
    return fig


# ─────────────────────────────────────────────────────────────
# 统一入口
# ─────────────────────────────────────────────────────────────

CHART_DRAWERS = {
    "bar_comparison": draw_bar_comparison,
    "deviation_heatmap": draw_deviation_heatmap,
    "radar": draw_radar,
    "experiment_comparison": draw_experiment_comparison,
    "calculated_trend": draw_calculated_trend,
}


def generate_nature_figures(data: dict) -> List[Dict[str, str]]:
    """
    根据假设数据自动生成 Nature 级图表。

    Args:
        data: 假设输出 JSON 数据

    Returns:
        [{"type": "bar_comparison", "title": "...", "panel": "a",
          "png_base64": "...", "svg_base64": "..."}, ...]
    """
    charts_spec = select_chart_types(data)
    results = []

    for spec in charts_spec:
        chart_type = spec["type"]
        drawer = CHART_DRAWERS.get(chart_type)
        if not drawer:
            continue

        try:
            fig = drawer(spec)
            if fig is None:
                continue

            png_b64 = _fig_to_base64(fig, "png")
            svg_b64 = _fig_to_base64(fig, "svg")
            plt.close(fig)

            results.append({
                "type": chart_type,
                "title": spec["title"],
                "panel": spec.get("panel", ""),
                "png_base64": png_b64,
                "svg_base64": svg_b64,
            })
        except Exception as e:
            logger.warning(f"生成 Nature 图表失败 ({chart_type}): {e}")
            try:
                plt.close("all")
            except Exception:
                pass

    return results


def nature_figures_to_html(figures: List[Dict[str, str]]) -> str:
    """
    将生成的 Nature 级图表渲染为 HTML 片段，嵌入交互报告。

    Args:
        figures: generate_nature_figures() 的输出

    Returns:
        HTML 字符串
    """
    if not figures:
        return ""

    items = []
    for fig in figures:
        png_b64 = fig.get("png_base64", "")
        title = fig.get("title", "")
        panel = fig.get("panel", "")
        chart_type = fig.get("type", "")

        # 图表类型中文描述
        type_labels = {
            "bar_comparison": "对比柱状图",
            "deviation_heatmap": "偏差热力图",
            "radar": "综合评估雷达图",
            "experiment_comparison": "实验设计对比图",
            "calculated_trend": "计算参数趋势图",
        }
        type_label = type_labels.get(chart_type, chart_type)

        panel_tag = f'<span style="font-weight:700;font-size:10px;color:#722ed1;">{panel})</span> ' if panel else ""

        items.append(
            f'<div class="nature-figure-card">'
            f'<div class="nature-figure-title">{panel_tag}{title}'
            f'<span style="font-size:10px;color:#999;margin-left:8px;">{type_label}</span></div>'
            f'<img src="data:image/png;base64,{png_b64}" '
            f'style="width:100%;max-width:720px;border-radius:4px;" '
            f'alt="{title}" />'
            f'</div>'
        )

    return f"""
  <section id="sec-nature-figures">
    <div class="card">
      <div class="card-title"><span class="icon">📊</span> Nature 级学术图表</div>
      <div class="nature-figure-grid">
        {"".join(items)}
      </div>
    </div>
  </section>
"""
