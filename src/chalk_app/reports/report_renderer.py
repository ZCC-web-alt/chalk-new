"""
交互式 HTML 报告渲染器。

将 HypothesisResult 数据注入 HTML 模板，生成可交互的网页报告。
支持 LLM 增强摘要（Qwen3.7-max）。

渲染策略：
  - 结构层（布局、导航、章节框架）→ Python 模板硬编码
  - 内容层（核心洞察、教学解读）→ 可选 LLM 增强
"""

import json
import os
import re
import html as html_mod
from datetime import datetime
from typing import Optional, Dict, Any, List

from llm_client import _chat, LLMConfig, _ensure_config


# ─────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────

_REPORT_VERSION = "1.0"

# 推理步骤类型 → 中文标签 + 颜色
_STEP_META = {
    "observation":    {"label": "🔍 观察", "color": "#1890ff"},
    "principle":      {"label": "📐 原理", "color": "#722ed1"},
    "induction":      {"label": "🔄 归纳", "color": "#13c2c2"},
    "deduction":      {"label": "➡️ 演绎", "color": "#fa8c16"},
    "abduction":      {"label": "🔎 溯因", "color": "#f5222d"},
}

# 教学提示（按推理类型）
_TEACHING_HINTS = {
    "observation": "这是一个实验观察——注意检查数据来源和测量条件，思考是否有其他可能的解读方式",
    "principle":   "这是一个理论原理——它是经过大量验证的规律，注意它的适用范围和边界条件",
    "induction":   "这是归纳推理——从特例总结一般规律，注意样本是否足够、是否有反例",
    "deduction":   "这是演绎推理——从一般原理推导特定结论，检查前提条件是否在当前场景下成立",
    "abduction":   "这是溯因推理——从结果反推最可能的原因，注意是否还有其他同样合理的解释",
}

# 置信度区间 → 颜色
def _conf_color(pct: float) -> str:
    if pct >= 80: return "#27ae60"
    if pct >= 60: return "#fa8c16"
    return "#f5222d"


# ─────────────────────────────────────────────────────────────
# HTML 模板（CSS + 骨架）
# ─────────────────────────────────────────────────────────────

_CSS_TEMPLATE = r"""
  :root {
    --primary: #1890ff; --primary-light: #e6f7ff;
    --secondary: #722ed1; --secondary-light: #f0e6ff;
    --success: #27ae60; --warning: #fa8c16; --danger: #f5222d;
    --bg: #f5f7fa; --card: #ffffff; --text: #1f2937;
    --text-sec: #6b7280; --border: #e5e7eb;
    --shadow: 0 1px 3px rgba(0,0,0,0.08);
    --shadow-lg: 0 10px 15px -3px rgba(0,0,0,0.1);
    --radius: 12px;
    /* 垂直节奏基准 */
    --base-font: 16px;
    --base-line: 1.75;
    --rhythm: calc(var(--base-font) * var(--base-line)); /* 28px 基准节奏单元 */
  }
  * { margin:0; padding:0; box-sizing:border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    background: var(--bg);
    color: var(--text);
    font-size: var(--base-font);
    line-height: var(--base-line);
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
  }
  /* 段落间距 > 行高，建立清晰垂直节奏 */
  p { margin-bottom: var(--rhythm); }
  ul, ol { margin-bottom: var(--rhythm); padding-left: 1.5em; }
  li { margin-bottom: 0.35em; }
  h1, h2, h3, h4 { line-height: 1.4; margin-top: calc(var(--rhythm) * 1.5); margin-bottom: calc(var(--rhythm) * 0.75); }
  h1 { font-size: 1.75rem; }
  h2 { font-size: 1.375rem; }
  h3 { font-size: 1.125rem; }
  h4 { font-size: 1rem; }

  .sidebar { position:fixed; left:0; top:0; bottom:0; width:260px; background:linear-gradient(180deg,#1a1a2e,#16213e); color:#fff; padding:20px 0; z-index:100; overflow-y:auto; display:flex; flex-direction:column; }
  .sidebar-logo { padding:0 20px 24px; border-bottom:1px solid rgba(255,255,255,.1); margin-bottom:16px; }
  .sidebar-logo h1 { font-size:20px; font-weight:700; color:#1890ff; }
  .sidebar-logo span { font-size:11px; color:rgba(255,255,255,.5); }
  .sidebar-nav { flex:1; padding-bottom:8px; }
  .nav-group { margin-bottom:4px; }
  .nav-group-toggle {
    width:100%; display:flex; align-items:center; justify-content:space-between; gap:8px;
    padding:10px 16px 10px 20px; color:rgba(255,255,255,.78); background:transparent;
    border:0; border-left:3px solid transparent; text-align:left; font-size:13px;
    font-family:inherit; line-height:1.35; cursor:pointer; transition:all .2s;
  }
  .nav-group-toggle:hover,.nav-group-toggle.active { color:#fff; background:rgba(255,255,255,.08); border-left-color:var(--primary); }
  .nav-main { display:flex; align-items:center; min-width:0; }
  .nav-main span:last-child { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .nav-caret { flex-shrink:0; font-size:10px; opacity:.7; transform:rotate(0deg); transition:transform .2s; }
  .nav-group.collapsed .nav-caret { transform:rotate(-90deg); }
  .nav-subitems { display:block; padding:2px 0 6px; }
  .nav-group.collapsed .nav-subitems { display:none; }
  .nav-subitem {
    display:block; padding:6px 16px 6px 46px; color:rgba(255,255,255,.55);
    text-decoration:none; font-size:12px; line-height:1.35; border-left:3px solid transparent;
    cursor:pointer; transition:all .2s;
  }
  .nav-subitem:hover,.nav-subitem.active { color:#fff; background:rgba(255,255,255,.06); border-left-color:rgba(24,144,255,.75); }
  .nav-icon { margin-right:8px; }
  .sidebar-footer { padding:16px 20px; border-top:1px solid rgba(255,255,255,.1); font-size:11px; color:rgba(255,255,255,.4); }

  /* 主内容区：居中 + 最大宽度限制 */
  .main {
    margin-left: 260px;
    padding: 40px 48px;
    max-width: 1080px;
  }
  /* 内容居中容器：正文阅读区限宽 800px */
  .main-inner {
    max-width: 800px;
    margin: 0 auto;
  }

  .topbar { display:flex; justify-content:space-between; align-items:center; margin-bottom:calc(var(--rhythm) * 1.5); padding-bottom:20px; border-bottom:2px solid var(--border); }
  .topbar h2 { font-size:22px; font-weight:700; }
  .topbar .meta { font-size:13px; color:var(--text-sec); margin-top:4px; }
  .topbar-actions { display:flex; gap:8px; }
  .btn { padding:8px 16px; border-radius:8px; border:none; cursor:pointer; font-size:13px; font-weight:500; transition:all .2s; display:inline-flex; align-items:center; gap:4px; }
  .btn-primary { background:var(--primary); color:#fff; }
  .btn-primary:hover { background:#096dd9; }
  .btn-secondary { background:var(--card); color:var(--text); border:1px solid var(--border); }
  .btn-secondary:hover { border-color:var(--primary); color:var(--primary); }

  /* 卡片：更大内边距 */
  .card {
    background: var(--card);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    padding: 2rem;
    margin-bottom: calc(var(--rhythm) * 1.5);
    border: 1px solid var(--border);
    transition: box-shadow .3s;
  }
  .card:hover { box-shadow:var(--shadow-lg); }
  .card-title { font-size:17px; font-weight:600; margin-bottom:calc(var(--rhythm) * 0.75); display:flex; align-items:center; gap:8px; }
  .card-title .icon { font-size:20px; }

  .report-group { margin-bottom: calc(var(--rhythm) * 2); scroll-margin-top: 24px; }
  .group-heading { margin: 0 0 18px; padding-bottom: 12px; border-bottom: 2px solid var(--border); }
  .group-kicker { font-size: 12px; font-weight: 700; color: var(--primary); letter-spacing: .04em; text-transform: uppercase; margin-bottom: 4px; }
  .group-heading h2 { margin: 0; font-size: 24px; line-height: 1.35; }
  .group-desc { margin: 6px 0 0; color: var(--text-sec); font-size: 13px; line-height: 1.7; }
  .medium-block { scroll-margin-top: 24px; }
  .mini-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
  .mini-block { padding:14px; background:#fafbfc; border:1px solid var(--border); border-radius:8px; font-size:13px; line-height:1.7; }
  .mini-title { font-size:12px; font-weight:700; color:#374151; margin-bottom:6px; }
  .compact-table { width:100%; border-collapse:collapse; font-size:12px; }
  .compact-table th { text-align:left; padding:8px; background:#f8fafc; border-bottom:1px solid var(--border); color:#374151; }
  .compact-table td { padding:8px; border-bottom:1px solid var(--border); vertical-align:top; }
  .schema-field-list { display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; }
  .schema-chip { display:inline-flex; padding:3px 8px; border-radius:6px; background:#eef2ff; color:#3730a3; font-size:11px; }

  .tag { display:inline-block; padding:2px 10px; border-radius:12px; font-size:11px; font-weight:500; }
  .tag-blue { background:var(--primary-light); color:var(--primary); }
  .tag-purple { background:var(--secondary-light); color:var(--secondary); }
  .tag-green { background:#e8f5e9; color:var(--success); }
  .tag-orange { background:#fff7e6; color:var(--warning); }
  .tag-red { background:#fff1f0; color:var(--danger); }

  .hypothesis-header { display:flex; gap:24px; align-items:flex-start; }
  .hypothesis-text { flex:1; }
  .hypothesis-text h3 { font-size:18px; margin-bottom:8px; line-height:1.5; }
  .hypothesis-text p { font-size:15px; color:var(--text-sec); }
  .radar-container { width:280px; height:240px; flex-shrink:0; }

  .chain-flow { display:flex; flex-direction:column; gap:0; }
  .chain-step { display:flex; align-items:stretch; gap:16px; }
  .chain-line { width:40px; display:flex; flex-direction:column; align-items:center; flex-shrink:0; }
  .chain-dot { width:14px; height:14px; border-radius:50%; border:3px solid; background:var(--card); z-index:2; flex-shrink:0; }
  .chain-connector { width:2px; flex:1; background:var(--border); min-height:16px; }
  .chain-body { flex:1; padding:14px 18px; border-radius:8px; margin-bottom:8px; border-left:4px solid; background:#fafbfc; cursor:pointer; transition:all .2s; }
  .chain-body:hover { box-shadow:var(--shadow); transform:translateX(2px); }
  .chain-body .step-type { font-size:12px; font-weight:600; text-transform:uppercase; letter-spacing:.5px; }
  .chain-body .step-content { font-size:15px; margin-top:4px; }
  .chain-body .step-meta { font-size:12px; color:var(--text-sec); margin-top:6px; display:flex; gap:12px; }
  .chain-detail { display:none; padding:14px; margin-top:8px; background:#f0f5ff; border-radius:6px; font-size:14px; line-height:1.7; }
  .chain-detail.show { display:block; }

  .debate-round { margin-bottom:calc(var(--rhythm) * 0.75); }
  .debate-round-title { font-size:14px; font-weight:600; color:var(--text-sec); margin-bottom:10px; }
  .debate-cards { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
  .debate-card { padding:18px; border-radius:10px; font-size:14px; line-height:1.7; border:1px solid; }
  .debate-optimist { background:#f0f9eb; border-color:#b7eb8f; }
  .debate-devil { background:#fff1f0; border-color:#ffa39e; }
  .debate-card .stance { font-weight:700; margin-bottom:8px; font-size:12px; text-transform:uppercase; letter-spacing:.5px; }
  .debate-optimist .stance { color:var(--success); }
  .debate-devil .stance { color:var(--danger); }
  .debate-verdict { margin-top:10px; padding:12px 18px; background:#fafbfc; border-radius:8px; font-size:14px; border-left:3px solid var(--secondary); }

  .verify-table { width:100%; border-collapse:collapse; font-size:14px; }
  .verify-table th { background:#fafbfc; padding:12px 14px; text-align:left; font-weight:600; border-bottom:2px solid var(--border); }
  .verify-table td { padding:12px 14px; border-bottom:1px solid var(--border); }
  .verify-table tr:hover { background:var(--primary-light); }
  .diff-low { color:var(--success); font-weight:600; }
  .diff-mid { color:var(--warning); font-weight:600; }
  .diff-high { color:var(--danger); font-weight:600; }

  .ref-search { width:100%; padding:12px 16px; border:1px solid var(--border); border-radius:8px; font-size:15px; margin-bottom:calc(var(--rhythm) * 0.75); }
  .ref-search:focus { outline:none; border-color:var(--primary); box-shadow:0 0 0 3px rgba(24,144,255,.1); }
  .ref-item { padding:12px 0; border-bottom:1px solid var(--border); font-size:14px; }
  .ref-item .ref-title { font-weight:600; color:var(--primary); cursor:pointer; }
  .ref-item .ref-meta { color:var(--text-sec); font-size:12px; margin-top:3px; }
  .source-doc-block { background:#f8fbff; border:1px solid #d6e8ff; border-radius:8px; padding:12px 14px; margin-bottom:14px; }
  .source-doc-heading { font-size:13px; font-weight:700; color:#075985; margin-bottom:8px; }
  .source-doc-note { color:var(--text-sec); font-size:12px; margin-top:8px; margin-bottom:0; }
  .evidence-summary { display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin-bottom:16px; }
  .evidence-metric { background:#fafbfc; border:1px solid var(--border); border-radius:8px; padding:10px 12px; }
  .evidence-metric .num { font-size:18px; font-weight:700; color:var(--primary); }
  .evidence-metric .label { font-size:11px; color:var(--text-sec); }
  .evidence-table { width:100%; border-collapse:collapse; font-size:12px; margin-top:12px; }
  .evidence-table th { background:#fafbfc; padding:8px; text-align:left; border-bottom:1px solid var(--border); }
  .evidence-table td { padding:8px; border-bottom:1px solid var(--border); vertical-align:top; }
  .execution-log { background:#f7f8fa; border:1px solid var(--border); border-radius:8px; padding:12px; margin-top:14px; font-size:12px; }
  .execution-log ul { margin:8px 0 0 18px; padding:0; }
  .hitl-round { border:1px solid var(--border); border-radius:8px; padding:16px; background:#fafbfc; margin-bottom:14px; }
  .hitl-title { display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap; font-size:14px; font-weight:700; margin-bottom:8px; }
  .hitl-meta { color:var(--text-sec); font-size:12px; font-weight:500; }
  .hitl-review { display:flex; flex-wrap:wrap; gap:6px; margin:8px 0 10px; }
  .hitl-review span { background:#eef2ff; color:#4f46e5; border-radius:6px; padding:3px 8px; font-size:11px; }
  .hitl-diff { width:100%; border-collapse:collapse; font-size:12px; margin-top:8px; }
  .hitl-diff th { text-align:left; background:#fff; padding:6px; border-bottom:1px solid var(--border); }
  .hitl-diff td { vertical-align:top; padding:6px; border-bottom:1px solid var(--border); }
  .qwen-tool-grid { display:grid; grid-template-columns:repeat(2,1fr); gap:10px; }
  .qwen-tool-card { background:#fafbfc; border:1px solid var(--border); border-radius:8px; padding:12px; font-size:13px; }
  .qwen-tool-card b { color:var(--primary); }
  .score-chart-grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-top:12px; }
  .score-chart { height:300px; min-height:260px; border:1px solid var(--border); border-radius:8px; background:#fafbfc; }
  .score-total-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }
  .score-total { border:1px solid var(--border); border-radius:8px; padding:12px; background:#f8fbff; }
  .score-total .num { font-size:26px; font-weight:700; color:var(--primary); line-height:1.1; }
  .score-evidence { display:inline-flex; padding:2px 6px; border-radius:5px; background:#eef6ff; color:#075985; font-size:11px; margin:1px 3px 1px 0; }
  .mm-figure { padding:14px; background:#fafbfc; border:1px solid var(--border); border-radius:8px; margin-top:12px; }
  .mm-figure-title { font-weight:600; font-size:13px; margin-bottom:6px; color:#1f2937; }
  .mm-association { padding:10px 12px; background:#f6ffed; border-left:3px solid var(--success); border-radius:6px; margin-top:8px; font-size:13px; }
  .cite-link { color:#1890ff; text-decoration:none; font-weight:600; cursor:pointer; border-bottom:1px dotted #1890ff; }
  .cite-link:hover { color:#722ed1; border-bottom-color:#722ed1; }

  .ref-fidelity { display:inline-block; padding:1px 6px; border-radius:4px; font-size:10px; font-weight:600; }

  .conclusion-box { background:linear-gradient(135deg,#e8f5e9,#e3f2fd); border-radius:var(--radius); padding:24px 2rem; border-left:5px solid var(--success); }
  .conclusion-box h4 { color:var(--success); margin-bottom:10px; }

  .experiment-grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
  .experiment-card { padding:18px; background:#fafbfc; border-radius:8px; border:1px solid var(--border); }
  .experiment-card .exp-title { font-weight:600; font-size:15px; margin-bottom:6px; }
  .experiment-card .exp-desc { font-size:14px; color:var(--text-sec); line-height:1.7; }

  /* 正文段落样式 */
  .card p, .card li { font-size: 15px; line-height: 1.8; }
  .card p { margin-bottom: var(--rhythm); }
  .card ul, .card ol { margin-bottom: var(--rhythm); }

  @media print { .sidebar,.topbar-actions { display:none!important; } .main { margin-left:0; padding:16px; max-width:100%; } .main-inner { max-width:100%; } .card { break-inside:avoid; box-shadow:none; border:1px solid #ddd; } }
  @media (max-width: 900px) { .sidebar { display:none; } .main { margin-left:0; padding:24px 16px; max-width:100%; } .main-inner { max-width:100%; } .debate-cards { grid-template-columns:1fr; } .experiment-grid { grid-template-columns:1fr; } .score-chart-grid { grid-template-columns:1fr; } }
  @keyframes fadeIn { from{opacity:0;transform:translateY(10px)} to{opacity:1;transform:translateY(0)} }
  .card { animation:fadeIn .4s ease-out both; }
  .card:nth-child(2){animation-delay:.05s} .card:nth-child(3){animation-delay:.1s}
  .card:nth-child(4){animation-delay:.15s} .card:nth-child(5){animation-delay:.2s}
  .card:nth-child(6){animation-delay:.25s} .card:nth-child(7){animation-delay:.3s}
"""


_JS_TEMPLATE = r"""
<script>
// 侧边导航 — 三大块可展开目录
var navGroups = [
  {
    id: 'sec-foundation',
    children: ['sec-structured-extraction','sec-database-schema','sec-literature-network','sec-database-evidence','sec-mm-evidence']
  },
  {
    id: 'sec-hypothesis-plan',
    children: ['sec-problem','sec-rationale','sec-tech','sec-datasets','sec-paper-title','sec-paper-abstract','sec-methods','sec-experiment']
  },
  {
    id: 'sec-validation-trace',
    children: ['sec-verify','sec-experiment-record','sec-ref','sec-scitool','sec-workflow-package','sec-hypothesis-tree','sec-chain','sec-qwen-agent','sec-debate','sec-human-collab']
  }
];
var trackedSections = navGroups.reduce(function(acc, group){
  acc.push(group.id);
  return acc.concat(group.children);
}, []);
var mainNavItems = document.querySelectorAll('.nav-group-toggle');
var subNavItems = document.querySelectorAll('.nav-subitem');

function groupForSection(id) {
  for (var i = 0; i < navGroups.length; i++) {
    if (navGroups[i].id === id || navGroups[i].children.indexOf(id) >= 0) {
      return navGroups[i].id;
    }
  }
  return '';
}

function setActiveNav(id) {
  var groupId = groupForSection(id);
  mainNavItems.forEach(function(item){
    var active = item.dataset.target === groupId;
    item.classList.toggle('active', active);
    if (active) {
      var group = item.closest('.nav-group');
      if (group) group.classList.remove('collapsed');
    }
  });
  subNavItems.forEach(function(item){
    item.classList.toggle('active', item.dataset.target === id);
  });
}

function toggleNavGroup(button) {
  var group = button.closest('.nav-group');
  if (group) group.classList.toggle('collapsed');
}

function navTo(sel) {
  var el = document.querySelector(sel);
  if (el) {
    el.scrollIntoView({behavior:'smooth',block:'start'});
    setActiveNav(sel.replace('#',''));
  }
}

var scrollTimer = null;
window.addEventListener('scroll', function(){
  if (scrollTimer) return;
  scrollTimer = setTimeout(function(){
    scrollTimer = null;
    var current = '';
    trackedSections.forEach(function(id){
      var el = document.getElementById(id);
      if (el && el.getBoundingClientRect().top <= 190) current = id;
    });
    if (current) setActiveNav(current);
  }, 100);
});
setActiveNav('sec-foundation');

function toggleDetail(el) {
  var detail = el.querySelector('.chain-detail');
  if (detail) detail.classList.toggle('show');
}

function filterRefs(query) {
  var q = query.toLowerCase();
  document.querySelectorAll('.ref-item').forEach(function(item){
    var text = item.dataset.search || '';
    item.style.display = text.includes(q) ? '' : 'none';
  });
}

function exportHTML() {
  var content = '<!DOCTYPE html>' + document.documentElement.outerHTML;
  var blob = new Blob([content], {type:'text/html;charset=utf-8'});
  var a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'chalk_report.html';
  a.click();
  URL.revokeObjectURL(a.href);
}
</script>
"""


# ─────────────────────────────────────────────────────────────
# 渲染器主类
# ─────────────────────────────────────────────────────────────

class HTMLReportRenderer:
    """将 HypothesisResult 渲染为交互式 HTML 报告。"""

    def __init__(self, config: Optional[LLMConfig] = None):
        self.config = _ensure_config(config)
        self._enhanced = None  # LLM 增强结果缓存

    # ── 公共接口 ──────────────────────────────────────────

    def render(self, result, enhance: bool = True) -> str:
        """
        渲染完整 HTML 报告。

        Args:
            result: HypothesisResult 实例
            enhance: 是否调用 LLM 增强摘要

        Returns:
            完整 HTML 字符串
        """
        data = result.raw_json if hasattr(result, "raw_json") else result
        if isinstance(data, str):
            data = json.loads(data)
        self._bar_data = {"metrics": [], "predicted": [], "literature": []}

        # LLM 增强（可选，失败时静默降级）
        if enhance:
            self._enhanced = self._llm_enhance(data)

        title = self._esc(data.get("paper_title", "未命名假设"))
        confidence = data.get("confidence", 5)
        feasibility = data.get("feasibility", "中")
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        # 领域标签
        domain = data.get("_domain", "科学研究")
        conf_pct = confidence * 10
        conf_color = _conf_color(conf_pct)

        parts = []
        parts.append(f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Chalk 报告 — {title}</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>{_CSS_TEMPLATE}</style>
</head>
<body>
<nav class="sidebar">
  <div class="sidebar-logo"><h1>🧪 Chalk</h1><span>假设分析交互报告</span></div>
  <div class="sidebar-nav">
  <div class="nav-group">
    <button class="nav-group-toggle active" type="button" data-target="sec-foundation" onclick="navTo('#sec-foundation')">
      <span class="nav-main"><span class="nav-icon">📚</span><span>1. 研究依据</span></span>
      <span class="nav-caret" onclick="event.stopPropagation();toggleNavGroup(this)">▼</span>
    </button>
    <div class="nav-subitems">
      <a class="nav-subitem" href="javascript:void(0)" data-target="sec-structured-extraction" onclick="navTo('#sec-structured-extraction')">A. 多文献提取表</a>
      <a class="nav-subitem" href="javascript:void(0)" data-target="sec-database-schema" onclick="navTo('#sec-database-schema')">B. 自建数据库字段</a>
      <a class="nav-subitem" href="javascript:void(0)" data-target="sec-literature-network" onclick="navTo('#sec-literature-network')">C. 文献关系网络</a>
      <a class="nav-subitem" href="javascript:void(0)" data-target="sec-database-evidence" onclick="navTo('#sec-database-evidence')">D. 数据库证据</a>
      <a class="nav-subitem" href="javascript:void(0)" data-target="sec-mm-evidence" onclick="navTo('#sec-mm-evidence')">E. 多模态证据</a>
    </div>
  </div>
  <div class="nav-group">
    <button class="nav-group-toggle" type="button" data-target="sec-hypothesis-plan" onclick="navTo('#sec-hypothesis-plan')">
      <span class="nav-main"><span class="nav-icon">🧪</span><span>2. 假设方案</span></span>
      <span class="nav-caret" onclick="event.stopPropagation();toggleNavGroup(this)">▼</span>
    </button>
    <div class="nav-subitems">
      <a class="nav-subitem" href="javascript:void(0)" data-target="sec-problem" onclick="navTo('#sec-problem')">待研究问题（Problem Statement）</a>
      <a class="nav-subitem" href="javascript:void(0)" data-target="sec-rationale" onclick="navTo('#sec-rationale')">解决思路（Rationale）</a>
      <a class="nav-subitem" href="javascript:void(0)" data-target="sec-tech" onclick="navTo('#sec-tech')">必要的技术手段（Technical Details）</a>
      <a class="nav-subitem" href="javascript:void(0)" data-target="sec-datasets" onclick="navTo('#sec-datasets')">数据集（Datasets）： Source与Target</a>
      <a class="nav-subitem" href="javascript:void(0)" data-target="sec-paper-title" onclick="navTo('#sec-paper-title')">标题（Paper Title）</a>
      <a class="nav-subitem" href="javascript:void(0)" data-target="sec-paper-abstract" onclick="navTo('#sec-paper-abstract')">摘要（Paper Abstract）</a>
      <a class="nav-subitem" href="javascript:void(0)" data-target="sec-methods" onclick="navTo('#sec-methods')">方法论（Methods）</a>
      <a class="nav-subitem" href="javascript:void(0)" data-target="sec-experiment" onclick="navTo('#sec-experiment')">实验设计（Experiments）</a>
    </div>
  </div>
  <div class="nav-group">
    <button class="nav-group-toggle" type="button" data-target="sec-validation-trace" onclick="navTo('#sec-validation-trace')">
      <span class="nav-main"><span class="nav-icon">✅</span><span>3. 验证溯源</span></span>
      <span class="nav-caret" onclick="event.stopPropagation();toggleNavGroup(this)">▼</span>
    </button>
    <div class="nav-subitems">
      <a class="nav-subitem" href="javascript:void(0)" data-target="sec-verify" onclick="navTo('#sec-verify')">实验结果（Results）</a>
      <a class="nav-subitem" href="javascript:void(0)" data-target="sec-experiment-record" onclick="navTo('#sec-experiment-record')">N. 实验记录卡片</a>
      <a class="nav-subitem" href="javascript:void(0)" data-target="sec-ref" onclick="navTo('#sec-ref')">参考论文（References）</a>
      <a class="nav-subitem" href="javascript:void(0)" data-target="sec-scitool" onclick="navTo('#sec-scitool')">科学工具验证</a>
      <a class="nav-subitem" href="javascript:void(0)" data-target="sec-workflow-package" onclick="navTo('#sec-workflow-package')">计算建模准备</a>
      <a class="nav-subitem" href="javascript:void(0)" data-target="sec-hypothesis-tree" onclick="navTo('#sec-hypothesis-tree')">假设演化轨迹</a>
      <a class="nav-subitem" href="javascript:void(0)" data-target="sec-chain" onclick="navTo('#sec-chain')">推理链</a>
      <a class="nav-subitem" href="javascript:void(0)" data-target="sec-qwen-agent" onclick="navTo('#sec-qwen-agent')">Qwen 工具</a>
      <a class="nav-subitem" href="javascript:void(0)" data-target="sec-debate" onclick="navTo('#sec-debate')">辩论过程</a>
      <a class="nav-subitem" href="javascript:void(0)" data-target="sec-human-collab" onclick="navTo('#sec-human-collab')">人机协作</a>
    </div>
  </div>
  </div>
  <div class="sidebar-footer">生成时间：{now}<br>模型：Qwen3.7-max<br>v{_REPORT_VERSION}</div>
</nav>
<div class="main">
<div class="main-inner">
""")

        # ── 顶部栏 ──
        parts.append(f"""
  <div class="topbar">
    <div>
      <h2>{title}</h2>
      <div class="meta">{self._esc(domain)} · 生成于 {now} · 综合置信度 {conf_pct}%</div>
    </div>
    <div class="topbar-actions">
      <button class="btn btn-secondary" onclick="window.print()">🖨️ 打印/PDF</button>
      <button class="btn btn-primary" onclick="exportHTML()">💾 导出 HTML</button>
    </div>
  </div>
""")

        # ── 摘要概览（含雷达图 DOM，ECharts 依赖） ──
        parts.append(self._render_summary(data, confidence, feasibility, conf_pct, conf_color, domain))

        chain = result.reasoning_chain if hasattr(result, "reasoning_chain") else data.get("reasoning_chain")
        debate = result.debate_history if hasattr(result, "debate_history") else data.get("debate_history", [])
        interaction_history = result.interaction_history if hasattr(result, "interaction_history") else {}
        if not isinstance(interaction_history, dict) or not interaction_history.get("interactions"):
            interaction_history = data.get("_human_collaboration", interaction_history)

        parts.append(self._render_group(
            "sec-foundation",
            "Foundation",
            "1. 研究依据与数据底座",
            "先看证据池、数据库字段和文献关系网络，避免假设脱离真实文献与可追溯数据。",
            [
                self._render_structured_extraction_table(data),
                self._render_database_schema(data),
                self._render_literature_network(data),
                self._render_database_evidence(data),
                self._render_multimodal_evidence(data),
            ],
        ))

        parts.append(self._render_group(
            "sec-hypothesis-plan",
            "Hypothesis",
            "2. 假设、机理与实施方案",
            "集中展示十个必填字段中的问题、推理、技术路线、论文雏形、方法论和实验设计。",
            [
                self._render_problem_statement(data),
                self._render_rationale(data),
                self._render_tech_details(data),
                self._render_datasets(data),
                self._render_title_abstract(data),
                self._render_methods(data),
                self._render_experiments(data),
            ],
        ))

        parts.append(self._render_group(
            "sec-validation-trace",
            "Validation",
            "3. 结果验证、实验记录与溯源",
            "把计算/公式输出与原文献对比，并保留参考文献、实验记录卡片、工具调用和人机协作轨迹。",
            [
                self._render_score_breakdown(data),
                self._render_verify(data),
                self._render_experiment_record_card(data),
                self._render_refs(data),
                self._render_scientific_toolkit(data),
                self._render_workflow_package(data),
                self._render_hypothesis_tree(data),
                self._render_chain(chain),
                self._render_qwen_agent_tools(data.get("_qwen_agent_tool_context", {})),
                self._render_debate(debate),
                self._render_human_collaboration(interaction_history),
            ],
        ))

        parts.append("</div>")  # .main

        # ── ECharts 图表脚本 ──
        parts.append(self._render_charts_js(data, conf_pct))

        parts.append(_JS_TEMPLATE)
        parts.append("</div></div></body></html>")

        return "\n".join(parts)

    # ── 各节渲染 ─────────────────────────────────────────

    def _render_group(self, section_id: str, kicker: str, title: str, desc: str, blocks: List[str]) -> str:
        content = "\n".join(block for block in blocks if block)
        return f"""
  <section id="{self._esc(section_id)}" class="report-group">
    <div class="group-heading">
      <div class="group-kicker">{self._esc(kicker)}</div>
      <h2>{self._esc(title)}</h2>
      <p class="group-desc">{self._esc(desc)}</p>
    </div>
    {content}
  </section>
"""

    def _render_structured_extraction_table(self, data):
        rows = data.get("structured_extraction_table", [])
        if not isinstance(rows, list):
            rows = []

        if not rows:
            body = (
                '<div class="mini-block">'
                '<div class="mini-title">多文献结构化提取表</div>'
                '<div style="color:var(--text-sec);">暂无结构化提取数据；请导入多篇文献、摘要、DOI或实验记录后重新生成。</div>'
                '</div>'
            )
        else:
            table_rows = []
            for idx, row in enumerate(rows[:12], 1):
                if not isinstance(row, dict):
                    row = {"key_data": str(row)}
                table_rows.append(
                    "<tr>"
                    f"<td>{self._esc(row.get('paper_id') or f'P{idx}')}</td>"
                    f"<td>{self._esc(row.get('title', ''))}</td>"
                    f"<td>{self._esc(row.get('materials_or_reaction') or row.get('material_system') or row.get('reaction_type') or '')}</td>"
                    f"<td>{self._esc(row.get('key_data') or row.get('performance_data') or '')}</td>"
                    f"<td>{self._esc(row.get('mechanism', ''))}</td>"
                    f"<td>{self._esc(row.get('limitation', ''))}</td>"
                    f"<td>{self._esc(row.get('evidence_status') or row.get('reliability_level') or '需核验')}</td>"
                    "</tr>"
                )
            body = (
                '<table class="compact-table"><thead><tr>'
                '<th>ID</th><th>文献</th><th>体系/反应</th><th>关键数据</th><th>机理</th><th>局限</th><th>状态</th>'
                '</tr></thead><tbody>'
                + "".join(table_rows)
                + '</tbody></table>'
            )

        return f"""
  <section id="sec-structured-extraction" class="medium-block">
    <div class="card">
      <div class="card-title"><span class="icon">🧾</span> A. 多文献结构化提取表</div>
      {body}
    </div>
  </section>
"""

    def _render_database_schema(self, data):
        schema = data.get("database_schema", {})
        if not isinstance(schema, dict):
            schema = {}
        table_name = self._esc(schema.get("table_name", "literature_evidence"))
        purpose = self._esc(schema.get("purpose", "用于存储文献证据、性能指标、机理解释和可靠性等级。"))
        fields = schema.get("fields", [])
        if not isinstance(fields, list):
            fields = []

        chips = []
        for item in fields[:80]:
            if isinstance(item, dict):
                name = item.get("name", "")
            else:
                name = str(item)
            if name:
                chips.append(f'<span class="schema-chip">{self._esc(name)}</span>')
        if not chips:
            chips.append('<span class="schema-chip">待补充字段</span>')

        compliance = self._esc(schema.get(
            "compliance_note",
            "source_text 仅来自用户导入内容、公开元数据、开放全文或机构授权全文。",
        ))

        return f"""
  <section id="sec-database-schema" class="medium-block">
    <div class="card">
      <div class="card-title"><span class="icon">🗄️</span> B. 自建数据库字段建议</div>
      <div class="mini-grid">
        <div class="mini-block"><div class="mini-title">表名</div>{table_name}</div>
        <div class="mini-block"><div class="mini-title">用途</div>{purpose}</div>
      </div>
      <div class="schema-field-list">{''.join(chips)}</div>
      <div style="margin-top:12px;color:var(--text-sec);font-size:12px;">{compliance}</div>
    </div>
  </section>
"""

    def _render_literature_network(self, data):
        network = data.get("literature_network", {})
        if not isinstance(network, dict):
            network = {}
        node_groups = network.get("node_groups", {})
        edges = network.get("evidence_edges", [])
        network_text = self._esc(network.get("network_text", "暂无文献关系网络；需补充多篇文献后构建。"))

        group_html = []
        if isinstance(node_groups, dict):
            for name, values in list(node_groups.items())[:8]:
                vals = values if isinstance(values, list) else [values]
                joined = "；".join(self._esc(v) for v in vals[:6] if v) or "待补充"
                group_html.append(
                    f'<div class="mini-block"><div class="mini-title">{self._esc(name)}</div>{joined}</div>'
                )

        edge_rows = []
        if isinstance(edges, list):
            for edge in edges[:12]:
                if not isinstance(edge, dict):
                    edge_rows.append(f'<li>{self._esc(edge)}</li>')
                else:
                    edge_rows.append(
                        "<li>"
                        f"{self._esc(edge.get('from', 'A'))} → {self._esc(edge.get('to', 'B'))}: "
                        f"{self._esc(edge.get('relation', edge.get('evidence', '')))}"
                        "</li>"
                    )
        edge_html = (
            f'<ul style="margin-top:8px;">{"".join(edge_rows)}</ul>'
            if edge_rows
            else '<div style="color:var(--text-sec);">暂无边关系；请补充可对照的机制、实验和数据方法文献。</div>'
        )

        return f"""
  <section id="sec-literature-network" class="medium-block">
    <div class="card">
      <div class="card-title"><span class="icon">🕸️</span> C. 文献关系网络图文字版</div>
      <div class="mini-grid">{''.join(group_html)}</div>
      <div class="mini-block" style="margin-top:12px;">
        <div class="mini-title">证据关系边</div>
        {edge_html}
      </div>
      <div style="margin-top:12px;font-size:13px;line-height:1.8;color:#333;">{network_text.replace(chr(10), '<br>')}</div>
    </div>
  </section>
"""

    def _render_database_evidence(self, data):
        evidence = data.get("_database_evidence_context", {})
        if not isinstance(evidence, dict) or not evidence:
            return ""

        summary = evidence.get("summary", {}) if isinstance(evidence.get("summary", {}), dict) else {}
        literature_items = evidence.get("literature_evidence", [])
        domain_items = evidence.get("domain_evidence", [])
        computational_items = evidence.get("computational_catalysis_evidence", [])
        if not isinstance(literature_items, list):
            literature_items = []
        if not isinstance(domain_items, list):
            domain_items = []
        if not isinstance(computational_items, list):
            computational_items = []

        metrics = [
            ("已引用文献证据", summary.get("literature_evidence_count", len(literature_items))),
            ("自建数据库匹配证据", summary.get("domain_evidence_count", len(domain_items))),
            ("计算催化证据", summary.get("computational_catalysis_evidence_count", len(computational_items))),
            ("证据状态", "有匹配" if summary.get("has_evidence") else "证据不足/需核验"),
        ]
        metric_html = "".join(
            f'<div class="evidence-metric"><div class="num">{self._esc(value)}</div>'
            f'<div class="label">{self._esc(label)}</div></div>'
            for label, value in metrics
        )

        lit_rows = []
        for item in literature_items[:8]:
            if not isinstance(item, dict):
                continue
            lit_rows.append(
                "<tr>"
                f"<td>LE-{self._esc(item.get('id', ''))}</td>"
                f"<td>{self._esc(item.get('title', ''))}<br><span style='color:var(--text-sec);'>{self._esc(item.get('doi', ''))}</span></td>"
                f"<td>{self._esc(item.get('material_system') or item.get('reaction_type') or item.get('battery_type') or '')}</td>"
                f"<td>{self._esc(item.get('key_data') or item.get('key_mechanism') or '')}</td>"
                f"<td>{self._esc(item.get('reliability_level', '需核验'))}</td>"
                "</tr>"
            )
        lit_html = self._render_database_evidence_table(
            "已引用文献证据",
            ["编号", "文献/DOI", "体系/反应", "关键证据", "可靠性"],
            lit_rows,
            "暂无已入库的文献证据；请从用户导入文献、DOI、摘要或图表描述中抽取。",
        )

        domain_rows = []
        for item in domain_items[:8]:
            if not isinstance(item, dict):
                continue
            metric = " ".join(
                str(part)
                for part in [item.get("metric_name"), item.get("metric_value"), item.get("metric_unit")]
                if str(part or "").strip()
            )
            domain_rows.append(
                "<tr>"
                f"<td>DE-{self._esc(item.get('id', ''))}</td>"
                f"<td>{self._esc(item.get('material_system', ''))}</td>"
                f"<td>{self._esc(item.get('reaction_type') or item.get('battery_type') or item.get('ion_type') or '')}</td>"
                f"<td>{self._esc(metric)}</td>"
                f"<td>{self._esc(item.get('key_mechanism') or item.get('condition_text') or '')}</td>"
                f"<td>{self._esc(item.get('reliability_level', '需核验'))}</td>"
                "</tr>"
            )
        domain_html = self._render_database_evidence_table(
            "自建数据库匹配证据",
            ["编号", "材料体系", "方向", "指标", "机理/条件", "可靠性"],
            domain_rows,
            "暂无匹配的领域指标证据；后续可从文献提取表、实验记录和历史假设中补充。",
        )

        computational_rows = []
        for item in computational_items[:8]:
            if not isinstance(item, dict):
                continue
            value = item.get("adsorption_energy") or item.get("dft_energy") or item.get("predicted_value") or item.get("reference_value") or ""
            computational_rows.append(
                "<tr>"
                f"<td>CE-{self._esc(item.get('id', ''))}</td>"
                f"<td>{self._esc(item.get('dataset_name', ''))}<br><span class='tag tag-purple'>计算证据</span></td>"
                f"<td>{self._esc(item.get('task_type', ''))}</td>"
                f"<td>{self._esc(item.get('reaction_context', ''))}</td>"
                f"<td>{self._esc(item.get('material_system', ''))}</td>"
                f"<td>{self._esc(item.get('adsorbate', ''))}</td>"
                f"<td>{self._esc(value)}</td>"
                f"<td>{self._esc(item.get('notes', ''))}</td>"
                "</tr>"
            )
        computational_html = self._render_database_evidence_table(
            "计算催化证据",
            ["编号", "数据集", "任务", "反应", "材料", "吸附物", "能量/预测", "说明"],
            computational_rows,
            "暂无匹配的 OCP/FAIR-Chem 计算证据索引；当前不会下载 OC20/OC22/OC25 全量数据。",
        )

        catalog = evidence.get("source_catalog", {})
        computational_catalog = {}
        if isinstance(catalog, dict) and isinstance(catalog.get("computational"), dict):
            computational_catalog = catalog.get("computational", {})
        catalog_rows = []
        for name, meta in list(computational_catalog.items())[:8]:
            if not isinstance(meta, dict):
                meta = {"scope": str(meta)}
            url = str(meta.get("url", ""))
            url_html = (
                f'<a href="{self._esc(url)}" target="_blank" rel="noopener noreferrer">{self._esc(url)}</a>'
                if url
                else ""
            )
            catalog_rows.append(
                "<tr>"
                f"<td>{self._esc(name)}</td>"
                f"<td>{self._esc(meta.get('scope', ''))}</td>"
                f"<td>{url_html}</td>"
                f"<td>{self._esc(meta.get('note', ''))}</td>"
                "</tr>"
            )
        catalog_html = self._render_database_evidence_table(
            "计算数据源目录",
            ["数据源", "范围", "链接", "使用边界"],
            catalog_rows,
            "暂无计算数据源目录。",
        )

        warnings = evidence.get("warnings", []) if isinstance(evidence.get("warnings", []), list) else []
        warning_items = "".join(f"<li>{self._esc(str(w))}</li>" for w in warnings[:6])
        error = str(evidence.get("error", "") or "")
        if error:
            warning_items += f"<li>{self._esc(error)}</li>"
        warnings_html = ""
        if warning_items or not summary.get("has_evidence"):
            warnings_html = (
                '<div class="mini-block" style="margin-top:12px;border-left:3px solid var(--warning);">'
                '<div class="mini-title">证据不足/需核验条目</div>'
                f'<ul style="margin:6px 0 0 18px;">{warning_items or "<li>本次未检索到足够数据库证据。</li>"}</ul>'
                '</div>'
            )

        return f"""
  <section id="sec-database-evidence" class="medium-block">
    <div class="card">
      <div class="card-title"><span class="icon">🧭</span> D. 数据库证据</div>
      <div style="padding:12px 14px;background:#fff7e6;border:1px solid #ffd591;border-radius:8px;margin-bottom:14px;font-size:12px;color:#5f3b00;">
        OCP、FAIR-Chem、OC20、OC22、OC25、ODAC23 在本报告中只作为计算证据或数据源线索，
        不得直接等同于实验性能。
      </div>
      <div class="evidence-summary">{metric_html}</div>
      {lit_html}
      {domain_html}
      {computational_html}
      {catalog_html}
      {warnings_html}
    </div>
  </section>
"""

    def _render_database_evidence_table(self, title: str, headers: List[str], rows: List[str], empty_text: str) -> str:
        if rows:
            header_html = "".join(f"<th>{self._esc(header)}</th>" for header in headers)
            body = (
                '<table class="compact-table"><thead><tr>'
                + header_html
                + '</tr></thead><tbody>'
                + "".join(rows)
                + '</tbody></table>'
            )
        else:
            body = f'<div style="color:var(--text-sec);font-size:12px;">{self._esc(empty_text)}</div>'
        return (
            '<div class="mini-block" style="margin-top:12px;">'
            f'<div class="mini-title">{self._esc(title)}</div>'
            f'{body}</div>'
        )

    def _render_problem_statement(self, data):
        problem = self._render_text_with_citations(data.get("problem_statement", ""))
        if not problem or problem == html_mod.escape("（未指定）", quote=True):
            problem = "（未指定）"
        return f"""
  <section id="sec-problem">
    <div class="card">
      <div class="card-title"><span class="icon">🎯</span> 1. 待研究问题（Problem Statement）</div>
      <div style="font-size:14px;line-height:1.8;color:#333;">{problem.replace(chr(10), '<br>')}</div>
    </div>
  </section>
"""

    def _render_rationale(self, data):
        rationale = self._render_text_with_citations(data.get("rationale", ""))
        if not rationale or rationale == html_mod.escape("（未指定）", quote=True):
            rationale = "（未指定）"
        return f"""
  <section id="sec-rationale">
    <div class="card">
      <div class="card-title"><span class="icon">💡</span> 2. 解决思路（Rationale）</div>
      <div style="font-size:14px;line-height:1.8;color:#333;">{rationale.replace(chr(10), '<br>')}</div>
    </div>
  </section>
"""

    def _render_tech_details(self, data):
        tech = self._render_text_with_citations(data.get("technical_details", ""))
        if not tech or tech == html_mod.escape("（未指定）", quote=True):
            tech = "（未指定）"
        return f"""
  <section id="sec-tech">
    <div class="card">
      <div class="card-title"><span class="icon">🔧</span> 3. 必要的技术手段（Technical Details）</div>
      <div style="font-size:14px;line-height:1.8;color:#333;">{tech.replace(chr(10), '<br>')}</div>
    </div>
  </section>
"""

    def _render_datasets(self, data):
        datasets = data.get("datasets", {})
        if not isinstance(datasets, dict):
            datasets = {}
        source = self._esc(str(datasets.get("source", "（未指定）")))
        target = self._esc(str(datasets.get("target", "（未指定）")))
        return f"""
  <section id="sec-datasets">
    <div class="card">
      <div class="card-title"><span class="icon">📊</span> 4. 数据集（Datasets）</div>
      <div style="display:flex;gap:24px;flex-wrap:wrap;">
        <div style="flex:1;min-width:280px;padding:16px;background:#f6ffed;border-radius:8px;border-left:3px solid #52c41a;">
          <div style="font-weight:600;font-size:13px;color:#389e0d;margin-bottom:8px;">Source — 假设推演依据的历史数据</div>
          <div style="font-size:13px;line-height:1.7;color:#333;">{source.replace(chr(10), '<br>')}</div>
        </div>
        <div style="flex:1;min-width:280px;padding:16px;background:#e6f7ff;border-radius:8px;border-left:3px solid #1890ff;">
          <div style="font-weight:600;font-size:13px;color:#096dd9;margin-bottom:8px;">Target — 验证实验所需的拟采集数据特征</div>
          <div style="font-size:13px;line-height:1.7;color:#333;">{target.replace(chr(10), '<br>')}</div>
        </div>
      </div>
    </div>
  </section>
"""

    def _render_title_abstract(self, data):
        title = self._esc(data.get("paper_title", "未命名假设"))
        title_candidates = data.get("paper_titles", [])
        if not isinstance(title_candidates, list):
            title_candidates = []
        if data.get("paper_title") and data.get("paper_title") not in title_candidates:
            title_candidates = [data.get("paper_title")] + title_candidates
        candidate_items = "".join(
            f"<li>{self._esc(candidate)}</li>"
            for candidate in title_candidates[:3]
            if candidate
        )
        candidates_html = ""
        if candidate_items:
            candidates_html = (
                '<div style="margin:0 0 16px;padding:12px 16px;background:#f8fafc;border-radius:8px;">'
                '<div style="font-size:11px;color:#999;margin-bottom:4px;">Paper Title Candidates</div>'
                f'<ol style="margin:0 0 0 18px;padding:0;">{candidate_items}</ol>'
                '</div>'
            )
        abstract = self._esc(data.get("paper_abstract", ""))
        if not abstract:
            abstract = "（未指定）"
        return f"""
  <section id="sec-title-abstract">
    <div class="card">
      <div class="card-title"><span class="icon">📄</span> 5. 标题与摘要</div>
      <div id="sec-paper-title" style="margin-bottom:16px;padding:12px 16px;background:#fafafa;border-radius:8px;scroll-margin-top:24px;">
        <div style="font-size:11px;color:#999;margin-bottom:4px;">Paper Title</div>
        <div style="font-size:16px;font-weight:600;color:#1a1a1a;">{title}</div>
      </div>
      {candidates_html}
      <div id="sec-paper-abstract" style="padding:12px 16px;background:#fafafa;border-radius:8px;scroll-margin-top:24px;">
        <div style="font-size:11px;color:#999;margin-bottom:4px;">Paper Abstract</div>
        <div style="font-size:13px;line-height:1.8;color:#333;">{abstract.replace(chr(10), '<br>')}</div>
      </div>
    </div>
  </section>
"""

    def _render_methods(self, data):
        methods = self._render_text_with_citations(data.get("methods", ""))
        if not methods or methods == html_mod.escape("（未指定）", quote=True):
            methods = "（未指定）"
        return f"""
  <section id="sec-methods">
    <div class="card">
      <div class="card-title"><span class="icon">⚙️</span> 6. 方法论（Methods）</div>
      <div style="font-size:14px;line-height:1.8;color:#333;">{methods.replace(chr(10), '<br>')}</div>
    </div>
  </section>
"""

    def _render_experiment_record_card(self, data):
        record = data.get("experiment_record_card", {})
        if not isinstance(record, dict):
            record = {}
        else:
            record = dict(record)

        database_ids = self._database_evidence_ids(data)
        if database_ids and not record.get("database_evidence_ids"):
            record["database_evidence_ids"] = database_ids

        if not record:
            return """
  <section id="sec-experiment-record" class="medium-block">
    <div class="card">
      <div class="card-title"><span class="icon">🧪</span> N. 可直接进入实验记录的假设卡片</div>
      <p style="color:var(--text-sec);">暂无实验记录卡片；请重新生成或在 HITL 审核中补充实验记录字段。</p>
    </div>
  </section>
"""

        preferred = [
            "hypothesis_id",
            "research_direction",
            "database_evidence_ids",
            "material_system",
            "reaction_type",
            "catalyst_system",
            "hypothesis_statement",
            "source_references",
            "variables_to_test",
            "control_group",
            "experimental_group",
            "synthesis_route",
            "cell_assembly",
            "electrode_preparation",
            "electrolyte_condition",
            "testing_protocol",
            "product_analysis",
            "characterization_methods",
            "expected_metrics",
            "risk_points",
            "result_record",
            "conclusion",
            "next_optimization_step",
        ]
        seen = set()
        rows = []
        for key in preferred + list(record.keys()):
            if key in seen or key not in record:
                continue
            seen.add(key)
            value = record.get(key)
            if isinstance(value, (list, tuple)):
                value_text = "；".join(str(v) for v in value)
            elif isinstance(value, dict):
                value_text = json.dumps(value, ensure_ascii=False)
            else:
                value_text = str(value or "")
            if not value_text:
                value_text = "待补充"
            rows.append(
                f"<tr><th>{self._esc(key)}</th><td>{self._render_text_with_citations(value_text).replace(chr(10), '<br>')}</td></tr>"
            )

        return f"""
  <section id="sec-experiment-record" class="medium-block">
    <div class="card">
      <div class="card-title"><span class="icon">🧪</span> N. 可直接进入实验记录的假设卡片</div>
      <table class="compact-table"><tbody>{"".join(rows)}</tbody></table>
    </div>
  </section>
"""

    @staticmethod
    def _database_evidence_ids(data: Dict[str, Any]) -> List[str]:
        evidence = data.get("_database_evidence_context", {})
        if not isinstance(evidence, dict):
            return []
        specs = [
            ("literature_evidence", "LE"),
            ("domain_evidence", "DE"),
            ("computational_catalysis_evidence", "CE"),
        ]
        ids = []
        seen = set()
        for key, prefix in specs:
            items = evidence.get(key, [])
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict) or item.get("id") in (None, ""):
                    continue
                evidence_id = f"{prefix}-{item.get('id')}"
                if evidence_id not in seen:
                    seen.add(evidence_id)
                    ids.append(evidence_id)
        return ids[:18]

    def _render_summary(self, data, confidence, feasibility, conf_pct, conf_color, domain):
        title = self._esc(data.get("paper_title", "未命名假设"))
        problem = self._esc(data.get("problem_statement", ""))
        rationale = self._esc(data.get("rationale", ""))
        refs = data.get("references", [])
        ref_count = len(refs) if refs else 0

        # 可行性标签颜色
        feas_tag = {"高": "tag-green", "中": "tag-orange", "低": "tag-red"}.get(feasibility, "tag-orange")

        # 参考文献标签
        if ref_count > 0:
            ref_tag_html = f'<span class="tag tag-blue">参考文献 {ref_count} 篇</span>'
        else:
            ref_tag_html = '<span class="tag tag-red">⚠ 无参考文献</span>'

        # LLM 增强的核心洞察
        insight_html = ""
        if self._enhanced and self._enhanced.get("insight"):
            insight_html = (
                f'<div style="margin-top:16px;padding:12px;background:#fffbe6;border-radius:8px;'
                f'font-size:13px;border-left:3px solid #faad14;">'
                f'<b>💡 核心洞察（AI 生成）：</b>{self._esc(self._enhanced["insight"])}</div>'
            )

        return f"""
  <section id="sec-summary">
    <div class="card">
      <div class="card-title"><span class="icon">📋</span> 假设摘要</div>
      <div class="hypothesis-header">
        <div class="hypothesis-text">
          <h3>{title}</h3>
          <p style="margin-top:8px;">
            <span class="tag tag-blue">{self._esc(domain)}</span>
            <span class="tag tag-purple">置信度 {conf_pct}%</span>
            <span class="tag {feas_tag}">可行性: {self._esc(feasibility)}</span>
            {ref_tag_html}
          </p>
          {insight_html}
        </div>
        <div class="radar-container" id="radarChart"></div>
      </div>
    </div>
  </section>
"""

    def _render_chain(self, chain):
        if not chain:
            return """
  <section id="sec-chain">
    <div class="card">
      <div class="card-title"><span class="icon">🔗</span> 推理链</div>
      <p style="color:var(--text-sec);">暂无推理链数据</p>
    </div>
  </section>
"""
        steps = chain.get("steps", []) if isinstance(chain, dict) else []
        if not steps:
            return """
  <section id="sec-chain">
    <div class="card">
      <div class="card-title"><span class="icon">🔗</span> 推理链</div>
      <p style="color:var(--text-sec);">暂无推理步骤</p>
    </div>
  </section>
"""

        parts = ["""
  <section id="sec-chain">
    <div class="card">
      <div class="card-title"><span class="icon">🔗</span> 推理链（""",
            str(len(steps)), """ 步）</div>
      <div class="chain-flow">
"""]

        for i, step in enumerate(steps):
            s = step if isinstance(step, dict) else {}
            sid = s.get("step_id", i + 1)
            stype = s.get("step_type", "observation")
            meta = _STEP_META.get(stype, {"label": "📌 步骤", "color": "#666"})
            content = self._esc(s.get("content", ""))
            conf = s.get("confidence", 0)
            conf_pct_val = int(conf * 100) if conf <= 1 else int(conf)
            conf_c = _conf_color(conf_pct_val)
            leads_to = s.get("leads_to", [])
            reasoning_path = self._esc(s.get("reasoning_path", ""))
            evidence = s.get("evidence", [])
            color = meta["color"]
            label = meta["label"]
            hint = _TEACHING_HINTS.get(stype, "")

            # LLM 教学解读（如有）
            teach_note = ""
            if self._enhanced and self._enhanced.get("teaching_notes"):
                teach_note = self._esc(self._enhanced["teaching_notes"].get(str(sid), ""))

            # 指向字符串
            leads_str = ", ".join(f"Step {t}" for t in leads_to) if leads_to else ""

            # 证据
            ev_items = "<br>".join(f"• {self._esc(str(ev))}" for ev in evidence[:3]) if evidence else ""

            # 详情块
            detail_parts = []
            detail_parts.append(f'<b>📖 教学解读：</b>{hint}')
            if teach_note:
                detail_parts.append(f'<br><b>🎓 AI 解读：</b>{teach_note}')
            if reasoning_path:
                detail_parts.append(f'<br><b>💡 推理路径：</b>{reasoning_path}')
            if ev_items:
                detail_parts.append(f'<br><b>📄 证据：</b>{ev_items}')

            is_last = (i == len(steps) - 1)
            connector = "" if is_last else '<div class="chain-connector"></div>'

            parts.append(f"""
        <div class="chain-step">
          <div class="chain-line"><div class="chain-dot" style="border-color:{color}"></div>{connector}</div>
          <div class="chain-body" style="border-color:{color}" onclick="toggleDetail(this)">
            <div class="step-type" style="color:{color}">{label} Step {sid}</div>
            <div class="step-content">{content}</div>
            <div class="step-meta"><span style="color:{conf_c}">置信度 {conf_pct_val}%</span>{f'<span>→ {leads_str}</span>' if leads_str else ''}</div>
            <div class="chain-detail">{"".join(detail_parts)}</div>
          </div>
        </div>
""")

        # 结论
        conclusion = ""
        if isinstance(chain, dict) and chain.get("conclusion"):
            conclusion = (
                f'<div style="margin-top:12px;padding:10px 14px;background:#e8f5e9;'
                f'border-left:4px solid #27ae60;border-radius:0 8px 8px 0;">'
                f'<b style="color:#27ae60;">📝 推理结论:</b> '
                f'<span style="font-size:13px;">{self._esc(chain["conclusion"])}</span></div>'
            )

        parts.append(f"""      </div>
      {conclusion}
    </div>
  </section>
""")
        return "".join(parts)

    def _render_debate(self, debate_history):
        if not debate_history:
            return """
  <section id="sec-debate">
    <div class="card">
      <div class="card-title"><span class="icon">⚔️</span> 辩论过程</div>
      <p style="color:var(--text-sec);">暂无辩论记录</p>
    </div>
  </section>
"""

        parts = [f"""
  <section id="sec-debate">
    <div class="card">
      <div class="card-title"><span class="icon">⚔️</span> 辩论过程（{len(debate_history)} 轮）</div>
"""]

        for i, round_data in enumerate(debate_history):
            r = round_data if isinstance(round_data, dict) else {}

            # 错误状态
            if r.get("error"):
                parts.append(f"""
      <div class="debate-round">
        <div class="debate-round-title">🔄 第 {i + 1} 轮辩论</div>
        <div style="padding:8px;background:#fff1f0;border-radius:8px;color:#f5222d;font-size:13px;">
          ⚠️ 辩论生成失败: {self._esc(str(r.get('error_message', '未知错误')))}
        </div>
      </div>
""")
                continue

            # 兼容嵌套和扁平结构
            devil = r.get("devil_advocate", {})
            attacks = devil.get("attack_points", []) or r.get("devil_attack_points", [])
            optimist_d = r.get("optimist", {})
            defenses = optimist_d.get("defense_points", []) or r.get("optimist_defense_points", [])

            summary = r.get("summary", {})
            if not summary or not isinstance(summary, dict) or not summary.get("verdict"):
                summary = {
                    "verdict": r.get("balance", "balanced"),
                    "key_insights": r.get("key_insights", []),
                    "recommendation": r.get("recommendation", ""),
                }

            # 反方攻击HTML
            devil_html = ""
            for atk in attacks:
                if isinstance(atk, dict):
                    severity = atk.get("severity", "中等")
                    target = self._esc(str(atk.get("target", "")))
                    evidence = self._esc(str(atk.get("evidence", "")))
                    devil_html += f"<div style='margin:4px 0;font-size:12px;'><b>[{severity}]</b> {target}"
                    if evidence:
                        devil_html += f"<br><span style='color:#666;'>证据: {evidence[:150]}</span>"
                    devil_html += "</div>"
                else:
                    devil_html += f"<div style='font-size:12px;'>• {self._esc(str(atk))}</div>"

            # 正方辩护HTML
            optimist_html = ""
            for dfn in defenses:
                if isinstance(dfn, dict):
                    counter = self._esc(str(dfn.get("counters_attack", "")))
                    evidence = self._esc(str(dfn.get("evidence", "")))
                    optimist_html += f"<div style='margin:4px 0;font-size:12px;'><b>反击:</b> {counter}"
                    if evidence:
                        optimist_html += f"<br><span style='color:#666;'>证据: {evidence[:150]}</span>"
                    optimist_html += "</div>"
                else:
                    optimist_html += f"<div style='font-size:12px;'>• {self._esc(str(dfn))}</div>"

            # 判定
            verdict_val = summary.get("verdict", "balanced")
            verdict_labels = {"favor_hypothesis": "支持假设", "favor_rejection": "倾向否定", "balanced": "暂无定论"}
            verdict_text = verdict_labels.get(verdict_val, verdict_val)
            recommendation = self._esc(str(summary.get("recommendation", "")))

            if not devil_html and not optimist_html:
                continue

            parts.append(f"""
      <div class="debate-round">
        <div class="debate-round-title">🔄 第 {i + 1} 轮辩论</div>
        <div class="debate-cards">
          <div class="debate-card debate-devil">
            <div class="stance">❌ 反方（魔鬼代言人）</div>
            {devil_html or '<span style="color:#999;">无攻击记录</span>'}
          </div>
          <div class="debate-card debate-optimist">
            <div class="stance">✅ 正方（乐观派）</div>
            {optimist_html or '<span style="color:#999;">无辩护记录</span>'}
          </div>
        </div>
        <div class="debate-verdict"><b>⚖️ 裁决：</b>{verdict_text}{f' — {recommendation}' if recommendation else ''}</div>
      </div>
""")

        parts.append("""    </div>
  </section>
""")
        return "".join(parts)

    def _render_human_collaboration(self, interaction_history):
        try:
            from hitl_trace import (
                ACTION_LABELS,
                STANCE_LABELS,
                CHANGE_LABELS,
                structured_feedback_items,
            )
        except Exception:
            ACTION_LABELS = {"approve": "批准", "revise": "反馈修订", "skip": "跳过"}
            STANCE_LABELS = {"neutral": "中立观察", "devil": "支持反方", "optimist": "支持正方", "custom": "自定义"}
            CHANGE_LABELS = {"added": "新增", "removed": "删除", "changed": "修改"}

            def structured_feedback_items(feedback):
                return [{"label": str(k), "value": str(v)} for k, v in (feedback or {}).items() if v and v != "—"]

        if not isinstance(interaction_history, dict):
            interaction_history = {}
        interactions = interaction_history.get("interactions", [])
        mode = interaction_history.get("mode", "auto")

        if not interactions:
            return f"""
  <section id="sec-human-collab">
    <div class="card">
      <div class="card-title"><span class="icon">🤝</span> 人机协作记录</div>
      <p style="color:var(--text-sec);">本次为{self._esc('人在回路' if mode == 'hitl' else '自动')}模式，未记录人工反馈轮次。</p>
    </div>
  </section>
"""

        parts = [f"""
  <section id="sec-human-collab">
    <div class="card">
      <div class="card-title"><span class="icon">🤝</span> 人机协作记录（{len(interactions)} 轮）</div>
      <p style="color:var(--text-sec);font-size:13px;">展示人类不是只点击批准，而是参与了假设辩论、证据核验与字段级修订。</p>
"""]

        for idx, entry in enumerate(interactions, 1):
            action = entry.get("user_action", "")
            stance = entry.get("debate_stance", "neutral")
            adopted = "是" if entry.get("final_adopted") else "否"
            review_badges = []
            for item in structured_feedback_items(entry.get("structured_feedback", {})):
                review_badges.append(
                    f"<span>{self._esc(item['label'])}: {self._esc(item['value'])}</span>"
                )
            comment = self._esc(entry.get("user_feedback_text", ""))
            comment_html = f"<p><b>人类反馈：</b>{comment}</p>" if comment else ""

            manual = entry.get("manual_revision", {})
            manual_diff = manual.get("diff") or entry.get("manual_revision_diff", [])
            manual_html = ""
            if manual_diff:
                manual_html = (
                    f"<p><b>人工直接修订：</b>{self._esc(manual.get('summary') or entry.get('manual_revision_summary', ''))}</p>"
                    + self._render_hitl_diff_table(manual_diff, CHANGE_LABELS)
                )

            ai_revision = entry.get("ai_revision", {})
            ai_html = ""
            if ai_revision:
                ai_html = (
                    f"<p><b>AI 如何修订：</b>{self._esc(ai_revision.get('summary', '已根据反馈修订'))}</p>"
                    + self._render_hitl_diff_table(ai_revision.get("diff", []), CHANGE_LABELS)
                )
            elif action == "approve":
                ai_html = "<p><b>AI 如何修订：</b>本轮被人类批准，进入后续验证或输出整理。</p>"
            elif action == "skip":
                ai_html = "<p><b>AI 如何修订：</b>本轮未收到人工修订要求，按智能体评审继续。</p>"

            parts.append(f"""
      <div class="hitl-round">
        <div class="hitl-title">
          <span>第 {self._esc(entry.get('round', idx))} 轮 · {self._esc(ACTION_LABELS.get(action, action))}</span>
          <span class="hitl-meta">立场：{self._esc(STANCE_LABELS.get(stance, stance))} · 最终采纳：{adopted}</span>
        </div>
        <div class="hitl-review">{"".join(review_badges) or '<span>未填写结构化评审表</span>'}</div>
        {comment_html}
        {manual_html}
        {ai_html}
      </div>
""")

        parts.append("""    </div>
  </section>
""")
        return "".join(parts)

    def _render_hitl_diff_table(self, diff_items, change_labels):
        if not diff_items:
            return "<p style='color:var(--text-sec);font-size:12px;'>未记录字段变化。</p>"
        rows = []
        for item in diff_items[:12]:
            rows.append(
                "<tr>"
                f"<td>{self._esc(item.get('field', ''))}</td>"
                f"<td>{self._esc(change_labels.get(item.get('change', ''), item.get('change', '变化')))}</td>"
                f"<td>{self._esc(item.get('before', ''))}</td>"
                f"<td>{self._esc(item.get('after', ''))}</td>"
                "</tr>"
            )
        return (
            "<table class='hitl-diff'><thead><tr>"
            "<th>字段</th><th>类型</th><th>修订前</th><th>修订后</th>"
            "</tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table>"
        )

    def _render_qwen_agent_tools(self, tool_context):
        if not isinstance(tool_context, dict) or not tool_context:
            return """
  <section id="sec-qwen-agent">
    <div class="card">
      <div class="card-title"><span class="icon">🧰</span> Qwen-Agent 工具调用</div>
      <p style="color:var(--text-sec);">当前环境未记录 Qwen-Agent 工具增强上下文。</p>
    </div>
  </section>
"""

        mode = self._esc(tool_context.get("mode", "unknown"))
        tools = tool_context.get("tools", [])
        tool_badges = "".join(
            f'<span class="tag tag-blue" style="margin-right:4px;">{self._esc(t)}</span>'
            for t in tools[:10]
        )

        cards = []
        for call in tool_context.get("tool_calls", [])[:6]:
            if not isinstance(call, dict):
                continue
            tool_name = self._esc(call.get("tool", "tool"))
            result = call.get("result", {})
            if not isinstance(result, dict):
                continue
            ok = result.get("ok")
            data = result.get("data")
            if isinstance(data, dict):
                if "context" in data:
                    body = self._esc(str(data.get("context", ""))[:420])
                else:
                    body = self._esc(json.dumps(data, ensure_ascii=False)[:420])
            elif isinstance(data, list):
                body = self._esc(json.dumps(data[:3], ensure_ascii=False)[:420])
            else:
                body = self._esc(str(data or result.get("error", ""))[:420])
            status = "通过" if ok else "失败"
            cards.append(
                f'<div class="qwen-tool-card"><b>{tool_name}</b> '
                f'<span class="tag {"tag-green" if ok else "tag-red"}">{status}</span>'
                f'<div style="margin-top:6px;color:var(--text-sec);">{body}</div></div>'
            )

        summary = self._esc(str(tool_context.get("summary", ""))[:1000])
        audit_html = self._render_qwen_agent_audit(tool_context.get("qwen_agent_audit", {}))
        summary_html = (
            f'<details style="margin-top:12px;font-size:13px;color:#555;">'
            f'<summary style="cursor:pointer;color:var(--primary);">查看工具增强上下文摘要</summary>'
            f'<pre style="white-space:pre-wrap;margin-top:8px;font-size:12px;">{summary}</pre></details>'
            if summary
            else ""
        )

        return f"""
  <section id="sec-qwen-agent">
    <div class="card">
      <div class="card-title"><span class="icon">🧰</span> Qwen-Agent 工具调用</div>
      <p style="color:var(--text-sec);font-size:13px;">模式：{mode} · 工具：{tool_badges or '未记录'}</p>
      <div class="qwen-tool-grid">{"".join(cards) or '<div class="qwen-tool-card">未记录单次工具调用明细。</div>'}</div>
      {audit_html}
      {summary_html}
    </div>
  </section>
"""

    def _render_qwen_agent_audit(self, audit: Any) -> str:
        if not isinstance(audit, dict):
            return ""
        claims = audit.get("claims", [])
        if not isinstance(claims, list) or not claims:
            return ""
        rows = []
        for claim in claims[:12]:
            if not isinstance(claim, dict):
                continue
            evidence_ids = claim.get("evidence_ids", [])
            if not isinstance(evidence_ids, list):
                evidence_ids = [evidence_ids]
            evidence_html = "".join(
                f'<span class="score-evidence">{self._esc(eid)}</span>'
                for eid in evidence_ids
                if str(eid or "").strip()
            )
            status = str(claim.get("support_status", "unsupported"))
            tag = "tag-green" if status == "supported" else "tag-red"
            rows.append(
                "<tr>"
                f"<td>{self._esc(claim.get('claim', ''))}</td>"
                f"<td><span class='tag {tag}'>{self._esc(status)}</span></td>"
                f"<td>{self._esc(claim.get('tool', ''))}</td>"
                f"<td>{evidence_html or self._esc('none')}</td>"
                f"<td>{self._esc(claim.get('notes', ''))}</td>"
                "</tr>"
            )
        if not rows:
            return ""
        return (
            '<div id="qwen-agent-audit" class="mini-block" style="margin-bottom:12px;">'
            '<div class="mini-title">Qwen-Agent evidence audit</div>'
            '<table class="compact-table"><thead><tr>'
            '<th>Claim</th><th>Status</th><th>Tool</th><th>Evidence IDs</th><th>Notes</th>'
            '</tr></thead><tbody>'
            + "".join(rows)
            + '</tbody></table></div>'
        )

    def _render_score_breakdown(self, data):
        breakdown = self._normalize_score_breakdown(data)
        confidence = breakdown.get("confidence", {})
        feasibility = breakdown.get("feasibility", {})
        conf_total = self._score_total(confidence)
        feas_total = self._score_total(feasibility)

        rows = []
        for category, payload in (("confidence", confidence), ("feasibility", feasibility)):
            for dim in payload.get("dimensions", []) if isinstance(payload.get("dimensions"), list) else []:
                if not isinstance(dim, dict):
                    continue
                evidence = dim.get("evidence_ids", [])
                if not isinstance(evidence, list):
                    evidence = [evidence]
                penalties = dim.get("penalties", [])
                if not isinstance(penalties, list):
                    penalties = [penalties]
                suggestions = dim.get("improvement_suggestions", [])
                if not isinstance(suggestions, list):
                    suggestions = [suggestions]
                evidence_html = "".join(
                    f'<span class="score-evidence">{self._esc(eid)}</span>'
                    for eid in evidence
                    if str(eid or "").strip()
                )
                rows.append(
                    "<tr>"
                    f"<td>{self._esc(category)}</td>"
                    f"<td><code>{self._esc(dim.get('id', ''))}</code><br>{self._esc(dim.get('label', ''))}</td>"
                    f"<td>{self._esc(dim.get('score', 0))}/{self._esc(dim.get('max_score', 0))}<br>"
                    f"<span class='tag tag-blue'>{self._esc(dim.get('level', ''))}</span></td>"
                    f"<td>{self._esc(dim.get('rationale', ''))}</td>"
                    f"<td>{evidence_html or self._esc('missing evidence')}</td>"
                    f"<td>{self._esc('; '.join(str(p) for p in penalties if p))}</td>"
                    f"<td>{self._esc('; '.join(str(s) for s in suggestions if s))}</td>"
                    "</tr>"
                )

        table = (
            '<table class="compact-table" style="margin-top:12px;"><thead><tr>'
            '<th>Type</th><th>Dimension</th><th>Score</th><th>Rationale</th><th>Evidence IDs</th><th>Penalties</th><th>Improve</th>'
            '</tr></thead><tbody>'
            + "".join(rows)
            + '</tbody></table>'
        )

        return f"""
  <section id="sec-score-breakdown" class="medium-block">
    <div class="card">
      <div class="card-title"><span class="icon">评分</span> 评分细则可视化</div>
      <div class="score-total-grid">
        <div class="score-total"><div class="mini-title">Confidence</div><div class="num">{self._esc(conf_total)}</div><div style="font-size:12px;color:var(--text-sec);">max 100</div></div>
        <div class="score-total"><div class="mini-title">Feasibility</div><div class="num">{self._esc(feas_total)}</div><div style="font-size:12px;color:var(--text-sec);">max 100</div></div>
      </div>
      <div class="score-chart-grid">
        <div id="scoreConfidenceChart" class="score-chart"></div>
        <div id="scoreFeasibilityChart" class="score-chart"></div>
      </div>
      {table}
    </div>
  </section>
"""

    def _normalize_score_breakdown(self, data: Dict[str, Any]) -> Dict[str, Any]:
        existing = data.get("score_breakdown", {}) if isinstance(data, dict) else {}
        if isinstance(existing, dict) and existing.get("confidence") and existing.get("feasibility"):
            return {
                "confidence": self._normalize_score_group(existing.get("confidence"), "confidence"),
                "feasibility": self._normalize_score_group(existing.get("feasibility"), "feasibility"),
            }

        confidence = data.get("confidence", 5) if isinstance(data, dict) else 5
        confidence_num = self._coerce_chart_number(confidence)
        if confidence_num is None:
            confidence_total = 50
        elif confidence_num <= 10:
            confidence_total = int(round(confidence_num * 10))
        else:
            confidence_total = int(round(confidence_num))
        feasibility = str(data.get("feasibility", "") if isinstance(data, dict) else "")
        feasibility_map = {
            "high": 85,
            "medium": 60,
            "low": 35,
            "楂?": 85,
            "高": 85,
            "涓?": 60,
            "中": 60,
            "浣?": 35,
            "低": 35,
        }
        feasibility_total = feasibility_map.get(feasibility, 60)
        return {
            "confidence": {
                "total_score": max(0, min(100, confidence_total)),
                "max_score": 100,
                "dimensions": [
                    {
                        "id": "legacy_confidence",
                        "label": "Legacy confidence",
                        "score": max(0, min(100, confidence_total)),
                        "max_score": 100,
                        "level": self._score_level(confidence_total, 100),
                        "rationale": "Rendered from legacy confidence field because score_breakdown was not available.",
                        "evidence_ids": self._database_evidence_ids(data) if isinstance(data, dict) else [],
                        "penalties": [],
                        "improvement_suggestions": ["Regenerate with score_breakdown for auditable sub-dimensions."],
                    }
                ],
            },
            "feasibility": {
                "total_score": max(0, min(100, feasibility_total)),
                "max_score": 100,
                "dimensions": [
                    {
                        "id": "legacy_feasibility",
                        "label": "Legacy feasibility",
                        "score": max(0, min(100, feasibility_total)),
                        "max_score": 100,
                        "level": self._score_level(feasibility_total, 100),
                        "rationale": f"Rendered from legacy feasibility field: {feasibility or 'unknown'}.",
                        "evidence_ids": [],
                        "penalties": [],
                        "improvement_suggestions": ["Add synthesis, protocol, characterization, cost and risk details."],
                    }
                ],
            },
        }

    def _normalize_score_group(self, group: Any, kind: str) -> Dict[str, Any]:
        group = dict(group) if isinstance(group, dict) else {}
        dims = []
        raw_dims = group.get("dimensions", [])
        if isinstance(raw_dims, dict):
            raw_dims = list(raw_dims.values())
        for idx, dim in enumerate(raw_dims if isinstance(raw_dims, list) else [], 1):
            if not isinstance(dim, dict):
                continue
            max_score = self._coerce_chart_number(dim.get("max_score", 0)) or 0
            score = self._coerce_chart_number(dim.get("score", 0)) or 0
            dims.append(
                {
                    "id": str(dim.get("id") or f"{kind}_{idx}"),
                    "label": str(dim.get("label") or dim.get("name") or dim.get("id") or f"{kind} {idx}"),
                    "score": round(float(score), 2),
                    "max_score": round(float(max_score), 2) if max_score else 100,
                    "level": str(dim.get("level") or self._score_level(score, max_score or 100)),
                    "rationale": str(dim.get("rationale") or dim.get("detail") or ""),
                    "evidence_ids": dim.get("evidence_ids", []) if isinstance(dim.get("evidence_ids", []), list) else [dim.get("evidence_ids")],
                    "penalties": dim.get("penalties", []) if isinstance(dim.get("penalties", []), list) else [dim.get("penalties")],
                    "improvement_suggestions": dim.get("improvement_suggestions", []) if isinstance(dim.get("improvement_suggestions", []), list) else [dim.get("improvement_suggestions")],
                }
            )
        total = self._coerce_chart_number(group.get("total_score", group.get("score", 0)))
        if total is None:
            total = sum(float(dim.get("score", 0)) for dim in dims)
        return {
            "total_score": round(float(total), 2),
            "max_score": self._coerce_chart_number(group.get("max_score", 100)) or 100,
            "dimensions": dims,
        }

    @staticmethod
    def _score_total(group: Dict[str, Any]) -> float:
        try:
            return round(float(group.get("total_score", 0)), 1)
        except Exception:
            return 0

    @staticmethod
    def _score_level(score: Any, max_score: Any) -> str:
        try:
            pct = float(score) / float(max_score or 100)
        except Exception:
            pct = 0
        if pct >= 0.8:
            return "high"
        if pct >= 0.55:
            return "medium"
        return "low"

    def _render_scientific_toolkit(self, data):
        toolkit = data.get("_scientific_toolkit", {})
        if not isinstance(toolkit, dict) or not toolkit:
            return """
  <section id="sec-scitool">
    <div class="card">
      <div class="card-title"><span class="icon">⚗️</span> 科学工具验证</div>
      <p style="color:var(--text-sec);">本次结果未包含 RDKit/pymatgen 科学工具验证。</p>
    </div>
  </section>
"""

        summary = toolkit.get("summary", {}) if isinstance(toolkit.get("summary", {}), dict) else {}
        versions = toolkit.get("tool_versions", {}) if isinstance(toolkit.get("tool_versions", {}), dict) else {}
        target_validation = toolkit.get("target_validation", {}) if isinstance(toolkit.get("target_validation", {}), dict) else {}
        target_html = ""
        if target_validation:
            target_lines = []
            for key, label in (
                ("materials", "materials"),
                ("molecules", "ligands/molecules"),
                ("active_sites", "active sites"),
                ("adsorbates", "adsorbates"),
                ("properties_to_validate", "properties"),
            ):
                values = target_validation.get(key, [])
                if isinstance(values, list) and values:
                    target_lines.append(f"{label}: " + ", ".join(self._esc(str(v)) for v in values[:8]))
            target_html = (
                '<div class="mini-block" style="margin-bottom:12px;">'
                f'<div class="mini-title">Target validation mode: {self._esc(target_validation.get("mode", "open_extraction"))}</div>'
                f'<div style="font-size:12px;color:var(--text-sec);">{self._esc(target_validation.get("source", ""))}</div>'
                f'<div style="margin-top:6px;font-size:12px;">{"<br>".join(target_lines)}</div>'
                '</div>'
            )
        metrics = [
            ("分子候选", summary.get("molecules_checked", 0)),
            ("材料候选", summary.get("materials_checked", 0)),
            ("生成结构", summary.get("structures_generated", 0)),
            ("解析结构", summary.get("structures_parsed", 0)),
            ("atomate2 工作流", summary.get("atomate2_workflows_planned", 0)),
            ("规划 Job", summary.get("atomate2_jobs_planned", 0)),
        ]
        metric_html = "".join(
            f'<div class="evidence-metric"><div class="num">{self._esc(value)}</div>'
            f'<div class="label">{self._esc(label)}</div></div>'
            for label, value in metrics
        )

        molecule_rows = []
        for mol in toolkit.get("molecules", [])[:6]:
            if not isinstance(mol, dict):
                continue
            desc = mol.get("descriptors", {}) if isinstance(mol.get("descriptors", {}), dict) else {}
            descriptor_text = ", ".join(
                f"{k}={v}" for k, v in list(desc.items())[:5]
            )
            warn = "; ".join(str(w) for w in mol.get("warnings", [])[:2]) if isinstance(mol.get("warnings", []), list) else ""
            molecule_rows.append(
                f"<tr><td>{self._esc(mol.get('input', ''))}</td>"
                f"<td>{self._esc(mol.get('status', ''))}</td>"
                f"<td>{self._esc(mol.get('formula', ''))}</td>"
                f"<td>{self._esc(descriptor_text or warn or mol.get('error', ''))}</td></tr>"
            )
        molecule_table = ""
        if molecule_rows:
            molecule_table = (
                '<table class="evidence-table"><thead><tr>'
                '<th>分子/SMILES</th><th>状态</th><th>分子式</th><th>RDKit 描述符/提示</th>'
                '</tr></thead><tbody>'
                + "".join(molecule_rows)
                + '</tbody></table>'
            )

        material_rows = []
        for mat in toolkit.get("materials", [])[:6]:
            if not isinstance(mat, dict):
                continue
            oxi = mat.get("oxidation_state_guesses", [])
            oxi_text = "; ".join(str(x) for x in oxi[:2]) if isinstance(oxi, list) else str(oxi)
            hint = oxi_text or "; ".join(str(w) for w in mat.get("warnings", [])[:2]) if isinstance(mat.get("warnings", []), list) else ""
            material_rows.append(
                f"<tr><td>{self._esc(mat.get('formula', ''))}</td>"
                f"<td>{self._esc(mat.get('status', ''))}</td>"
                f"<td>{self._esc(mat.get('reduced_formula', ''))}</td>"
                f"<td>{self._esc(mat.get('chemical_system', ''))}</td>"
                f"<td>{self._esc(hint or mat.get('error', ''))}</td></tr>"
            )
        material_table = ""
        if material_rows:
            material_table = (
                '<table class="evidence-table"><thead><tr>'
                '<th>材料式</th><th>状态</th><th>约化式</th><th>体系</th><th>氧化态/提示</th>'
                '</tr></thead><tbody>'
                + "".join(material_rows)
                + '</tbody></table>'
            )

        structure_rows = []
        for struct in toolkit.get("structure_analyses", [])[:6]:
            if not isinstance(struct, dict):
                continue
            lattice = struct.get("lattice", {}) if isinstance(struct.get("lattice", {}), dict) else {}
            lattice_text = ""
            if lattice:
                lattice_text = (
                    f"a={lattice.get('a')}, b={lattice.get('b')}, c={lattice.get('c')}, "
                    f"V={lattice.get('volume')}"
                )
            else:
                lattice_text = "; ".join(str(w) for w in struct.get("warnings", [])[:2]) if isinstance(struct.get("warnings", []), list) else ""
            structure_rows.append(
                f"<tr><td>{self._esc(struct.get('format', ''))}</td>"
                f"<td>{self._esc(struct.get('status', ''))}</td>"
                f"<td>{self._esc(struct.get('formula_pretty') or struct.get('formula', ''))}</td>"
                f"<td>{self._esc(lattice_text or struct.get('error', ''))}</td>"
                f"<td>{self._esc(struct.get('path', ''))}</td></tr>"
            )
        structure_table = ""
        if structure_rows:
            structure_table = (
                '<table class="evidence-table"><thead><tr>'
                '<th>格式</th><th>状态</th><th>结构式</th><th>晶格/提示</th><th>文件</th>'
                '</tr></thead><tbody>'
                + "".join(structure_rows)
                + '</tbody></table>'
            )

        atomate2 = toolkit.get("atomate2_dryrun", {}) if isinstance(toolkit.get("atomate2_dryrun", {}), dict) else {}
        atomate2_rows = []
        for workflow in atomate2.get("workflows", [])[:6]:
            if not isinstance(workflow, dict):
                continue
            source = workflow.get("source_structure", {}) if isinstance(workflow.get("source_structure", {}), dict) else {}
            job_names = workflow.get("job_names", [])
            job_text = ", ".join(str(name) for name in job_names[:4]) if isinstance(job_names, list) else str(job_names)
            atomate2_rows.append(
                f"<tr><td>{self._esc(workflow.get('workflow_type', ''))}</td>"
                f"<td>{self._esc(workflow.get('status', ''))}</td>"
                f"<td>{self._esc(source.get('formula', ''))}</td>"
                f"<td>{self._esc(workflow.get('job_count', 0))}</td>"
                f"<td>{self._esc(job_text)}</td>"
                f"<td>{self._esc(workflow.get('execution_policy', ''))}</td></tr>"
            )
        atomate2_table = ""
        if atomate2_rows:
            atomate2_table = (
                '<table class="evidence-table"><thead><tr>'
                '<th>atomate2 workflow</th><th>状态</th><th>来源结构</th><th>Job 数</th><th>Job 名称</th><th>执行策略</th>'
                '</tr></thead><tbody>'
                + "".join(atomate2_rows)
                + '</tbody></table>'
            )

        atomate2_warnings = atomate2.get("warnings", []) if isinstance(atomate2.get("warnings", []), list) else []
        atomate2_warning_html = ""
        if atomate2_warnings:
            atomate2_warning_html = (
                '<div style="margin-top:12px;padding:12px;background:#f6ffed;border:1px solid #b7eb8f;'
                'border-radius:8px;font-size:13px;">'
                '<b>atomate2 dry-run 诊断:</b> '
                + self._esc("; ".join(str(w) for w in atomate2_warnings[:4]))
                + '</div>'
            )

        plan_html = []
        for plan in toolkit.get("reproducibility_plan", [])[:3]:
            if not isinstance(plan, dict):
                continue
            potcar = plan.get("potcar_hints", {})
            potcar_text = ", ".join(f"{k}:{v}" for k, v in potcar.items()) if isinstance(potcar, dict) else ""
            plan_html.append(
                f'<div class="experiment-card"><div class="exp-title">VASP 复现建议 · {self._esc(plan.get("target", ""))}</div>'
                f'<div class="exp-desc">计算: {self._esc(plan.get("calculation", ""))}<br>'
                f'POTCAR: {self._esc(potcar_text)}<br>'
                f'KPOINTS: {self._esc(plan.get("kpoints_hint", ""))}<br>'
                f'{self._esc(plan.get("notes", ""))}</div></div>'
            )

        warnings = toolkit.get("warnings", [])
        warning_html = ""
        if warnings:
            warning_html = (
                '<div style="margin-top:14px;padding:12px;background:#fff7e6;border:1px solid #ffd591;'
                'border-radius:8px;font-size:13px;">'
                '<b>工具提示:</b> '
                + self._esc("; ".join(str(w) for w in warnings[:5]))
                + '</div>'
            )

        return f"""
  <section id="sec-scitool">
    <div class="card">
      <div class="card-title"><span class="icon">⚗️</span> 科学工具验证</div>
      <p style="color:var(--text-sec);font-size:13px;">
        RDKit: {self._esc(versions.get("rdkit", "unknown"))} ·
        pymatgen: {self._esc(versions.get("pymatgen", "unknown"))} ·
        atomate2: {self._esc(versions.get("atomate2", "unknown"))} ·
        jobflow: {self._esc(versions.get("jobflow", "unknown"))} ·
        ase: {self._esc(versions.get("ase", "unknown"))}
      </p>
      {target_html}
      <div class="evidence-summary">{metric_html}</div>
      {molecule_table}
      {material_table}
      {structure_table}
      {atomate2_table}
      {atomate2_warning_html}
      <div class="experiment-grid" style="margin-top:12px;">{"".join(plan_html)}</div>
      {warning_html}
    </div>
  </section>
"""

    def _render_workflow_package(self, data):
        package = data.get("_hypothesis_workflow_package", {})
        if not isinstance(package, dict) or not package:
            try:
                from hypothesis_workflow_exporter import build_hypothesis_workflow_package
                package = build_hypothesis_workflow_package(data)
            except Exception:
                package = {}
        if not isinstance(package, dict) or not package:
            return """
  <section id="sec-workflow-package">
    <div class="card">
      <div class="card-title"><span class="icon">🧱</span> 计算建模工作流准备</div>
      <p style="color:var(--text-sec);">本次结果未生成计算建模工作流包。</p>
    </div>
  </section>
"""

        summary = package.get("summary", {}) if isinstance(package.get("summary"), dict) else {}
        metrics = [
            ("候选结构", summary.get("structure_candidates", 0)),
            ("pymatgen 解析", summary.get("parsed_structures", 0)),
            ("VASP 建议", summary.get("vasp_recommendations", 0)),
            ("dry-run workflow", summary.get("atomate2_workflows", 0)),
            ("dry-run job", summary.get("atomate2_jobs", 0)),
        ]
        metric_html = "".join(
            f'<div class="evidence-metric"><div class="num">{self._esc(value)}</div>'
            f'<div class="label">{self._esc(label)}</div></div>'
            for label, value in metrics
        )

        structure_rows = []
        for item in package.get("structures", [])[:6]:
            if not isinstance(item, dict):
                continue
            analysis = item.get("analysis", {}) if isinstance(item.get("analysis"), dict) else {}
            lattice = analysis.get("lattice", {}) if isinstance(analysis.get("lattice"), dict) else {}
            lattice_text = ""
            if lattice:
                lattice_text = (
                    f"a={lattice.get('a')}, b={lattice.get('b')}, c={lattice.get('c')}, "
                    f"V={lattice.get('volume')}"
                )
            structure_rows.append(
                f"<tr><td>{self._esc(item.get('name', ''))}</td>"
                f"<td>{self._esc(item.get('format', ''))}</td>"
                f"<td>{self._esc(item.get('parse_status', 'unknown'))}</td>"
                f"<td>{self._esc(analysis.get('formula_pretty') or item.get('formula', ''))}</td>"
                f"<td>{self._esc(lattice_text or item.get('path', ''))}</td></tr>"
            )
        structure_table = ""
        if structure_rows:
            structure_table = (
                '<table class="evidence-table"><thead><tr>'
                '<th>结构</th><th>格式</th><th>pymatgen</th><th>组成</th><th>晶格/来源</th>'
                '</tr></thead><tbody>'
                + "".join(structure_rows)
                + '</tbody></table>'
            )

        vasp_rows = []
        for rec in package.get("vasp_recommendations", [])[:4]:
            if not isinstance(rec, dict):
                continue
            relax = rec.get("incar_relax", {}) if isinstance(rec.get("incar_relax"), dict) else {}
            scf = rec.get("incar_scf", {}) if isinstance(rec.get("incar_scf"), dict) else {}
            potcar = rec.get("potcar_hints", {}) if isinstance(rec.get("potcar_hints"), dict) else {}
            vasp_rows.append(
                f"<tr><td>{self._esc(rec.get('target', ''))}</td>"
                f"<td>{self._esc(relax.get('SYSTEM', 'Relax'))} / {self._esc(scf.get('SYSTEM', 'SCF'))}</td>"
                f"<td>{self._esc(rec.get('kpoints_hint', ''))}</td>"
                f"<td>{self._esc(', '.join(f'{k}:{v}' for k, v in potcar.items()))}</td></tr>"
            )
        vasp_table = ""
        if vasp_rows:
            vasp_table = (
                '<table class="evidence-table"><thead><tr>'
                '<th>目标</th><th>relax/scf</th><th>KPOINTS 建议</th><th>POTCAR hints</th>'
                '</tr></thead><tbody>'
                + "".join(vasp_rows)
                + '</tbody></table>'
            )

        atomate2 = package.get("atomate2_dryrun", {}) if isinstance(package.get("atomate2_dryrun"), dict) else {}
        atom_summary = atomate2.get("summary", {}) if isinstance(atomate2.get("summary"), dict) else {}
        atomate2_html = (
            '<div style="margin-top:12px;padding:12px;background:#f8fbff;border:1px solid #d6e8ff;'
            'border-radius:8px;font-size:13px;">'
            '<b>atomate2 dry-run:</b> '
            f"status={self._esc(atomate2.get('status', 'skipped'))}, "
            f"workflow_kind={self._esc(atomate2.get('workflow_kind', ''))}, "
            f"workflows={self._esc(atom_summary.get('workflows_planned', 0))}, "
            f"jobs={self._esc(atom_summary.get('jobs_planned', 0))}. "
            "<b>dry-run only / not submitted / must be reviewed</b>"
            "</div>"
        )

        risks = package.get("risk_notices", []) if isinstance(package.get("risk_notices"), list) else []
        risk_html = ""
        if risks:
            risk_html = (
                '<div style="margin-top:12px;padding:12px;background:#fff7e6;border:1px solid #ffd591;'
                'border-radius:8px;font-size:13px;"><b>风险提示:</b><ul style="margin:8px 0 0 18px;">'
                + "".join(f"<li>{self._esc(str(risk))}</li>" for risk in risks[:6])
                + "</ul></div>"
            )

        return f"""
  <section id="sec-workflow-package">
    <div class="card">
      <div class="card-title"><span class="icon">🧱</span> 计算建模工作流准备</div>
      <p style="color:var(--text-sec);font-size:13px;">
        这是给计算建模页和导出按钮复用的中间工作流包；只整理候选输入和 dry-run 元数据，不执行计算。
      </p>
      <div class="evidence-summary">{metric_html}</div>
      {structure_table}
      {vasp_table}
      {atomate2_html}
      {risk_html}
    </div>
  </section>
"""

    def _render_hypothesis_tree(self, data):
        tree = data.get("_hypothesis_tree_search", {})
        if not isinstance(tree, dict) or not tree.get("nodes"):
            return """
  <section id="sec-hypothesis-tree">
    <div class="card">
      <div class="card-title"><span class="icon">🌳</span> AI Scientist 假设演化轨迹</div>
      <p style="color:var(--text-sec);">本次结果未包含 Trace-first HypothesisTreeSearch 轨迹树。</p>
    </div>
  </section>
"""

        nodes = tree.get("nodes", []) if isinstance(tree.get("nodes"), list) else []
        summary = tree.get("summary", {}) if isinstance(tree.get("summary"), dict) else {}
        recommended_id = tree.get("recommended_node_id", "")
        metrics = [
            ("节点数", summary.get("node_count", len(nodes))),
            ("推荐阶段", summary.get("recommended_stage", "")),
            ("推荐 overall_score", summary.get("recommended_overall_score", 0)),
            ("工具就绪", "yes" if summary.get("tool_ready") else "review"),
        ]
        metric_html = "".join(
            f'<div class="evidence-metric"><div class="num">{self._esc(value)}</div>'
            f'<div class="label">{self._esc(label)}</div></div>'
            for label, value in metrics
        )

        stage_label = {
            "initial_hypothesis": "初始假设",
            "critic_review": "批判者质疑",
            "devil_advocate": "反方观点",
            "optimist_review": "乐观者辩护",
            "human_revision": "人工修订",
            "scientific_toolkit_review": "科学工具复核",
            "final_hypothesis": "最终假设",
        }
        rows = []
        for node in nodes[:14]:
            if not isinstance(node, dict):
                continue
            score = node.get("score", {}) if isinstance(node.get("score"), dict) else {}
            is_recommended = node.get("node_id") == recommended_id
            badge = '<span class="tag tag-green">recommended</span>' if is_recommended else ""
            stage = node.get("stage", "")
            support = node.get("support_evidence", [])
            objections = node.get("objections", [])
            support_count = len(support) if isinstance(support, list) else 0
            objection_count = len(objections) if isinstance(objections, list) else 0
            rows.append(
                f"<tr><td>{self._esc(stage_label.get(stage, stage))}<br>"
                f"<span style='font-size:10px;color:var(--text-sec);'>{self._esc(stage)}</span> {badge}</td>"
                f"<td>{self._esc(node.get('agent_role', ''))}</td>"
                f"<td><code>{self._esc(score.get('overall_score', 0))}</code><br>"
                f"<span style='font-size:10px;color:var(--text-sec);'>"
                f"evidence={self._esc(score.get('evidence_score', 0))}, "
                f"feasibility={self._esc(score.get('feasibility_score', 0))}</span></td>"
                f"<td>{self._esc(support_count)} / {self._esc(objection_count)}</td>"
                f"<td>{self._esc(node.get('decision', ''))}<br>"
                f"<span style='font-size:11px;color:var(--text-sec);'>{self._esc(str(node.get('decision_reason', ''))[:160])}</span></td></tr>"
            )

        table = (
            '<table class="evidence-table"><thead><tr>'
            '<th>阶段</th><th>来源 Agent</th><th>overall_score / 评分</th><th>支持/反对</th><th>决策</th>'
            '</tr></thead><tbody>'
            + "".join(rows)
            + '</tbody></table>'
        )

        return f"""
  <section id="sec-hypothesis-tree">
    <div class="card">
      <div class="card-title"><span class="icon">🌳</span> AI Scientist 假设演化轨迹</div>
      <p style="color:var(--text-sec);font-size:13px;">
        Trace-first HypothesisTreeSearch 记录真实管线轨迹：初始假设 → 批判 → 反方/正方辩论 → 人工修订 → 工具复核 → 最终假设。
      </p>
      <div class="evidence-summary">{metric_html}</div>
      {table}
    </div>
  </section>
"""

    def _render_multimodal_evidence(self, data):
        evidence = data.get("_multimodal_evidence", {})
        if not isinstance(evidence, dict) or not evidence:
            return """
  <section id="sec-mm-evidence">
    <div class="card">
      <div class="card-title"><span class="icon">🧾</span> 多模态证据</div>
      <p style="color:var(--text-sec);">本次结果未包含结构化多模态证据。</p>
    </div>
  </section>
"""

        summary = evidence.get("summary", {}) if isinstance(evidence.get("summary", {}), dict) else {}
        metrics = [
            ("分析图表", summary.get("figures_analyzed", 0)),
            ("清洗数据点", summary.get("cleaned_points", 0)),
            ("可疑数据点", summary.get("suspicious_points", 0)),
            ("跨源关联", summary.get("associations", 0)),
        ]
        metric_html = "".join(
            f'<div class="evidence-metric"><div class="num">{self._esc(value)}</div>'
            f'<div class="label">{self._esc(label)}</div></div>'
            for label, value in metrics
        )

        figure_html = []
        for fig in evidence.get("figures", [])[:6]:
            if not isinstance(fig, dict):
                continue
            rows = []
            for point in fig.get("points", [])[:8]:
                if not isinstance(point, dict):
                    continue
                status = (
                    '<span class="tag tag-orange">可疑</span>'
                    if point.get("is_suspicious")
                    else '<span class="tag tag-green">通过</span>'
                )
                rows.append(
                    f"<tr><td>{self._esc(point.get('parameter_cn') or point.get('parameter', ''))}</td>"
                    f"<td>{self._esc(point.get('value', ''))}</td>"
                    f"<td>{self._esc(point.get('unit', ''))}</td>"
                    f"<td>{self._esc(point.get('source', ''))}</td>"
                    f"<td>{status}</td></tr>"
                )
            table = ""
            if rows:
                table = (
                    '<table class="evidence-table"><thead><tr>'
                    '<th>参数</th><th>值</th><th>单位</th><th>来源</th><th>状态</th>'
                    '</tr></thead><tbody>'
                    + "".join(rows)
                    + '</tbody></table>'
                )
            if not table:
                table = '<div style="font-size:12px;color:var(--text-sec);margin-top:6px;">未提取到可用定量数据。</div>'
            figure_html.append(
                '<div class="mm-figure">'
                f'<div class="mm-figure-title">{self._esc(fig.get("source_label", ""))} '
                f'<span style="color:#888;font-weight:400;">{self._esc(fig.get("image_type", ""))}</span></div>'
                f'{table}</div>'
            )

        assoc_html = []
        for assoc in evidence.get("associations", [])[:5]:
            if not isinstance(assoc, dict):
                continue
            assoc_html.append(
                '<div class="mm-association">'
                f'<b>{self._esc(assoc.get("description", ""))}</b><br>'
                f'{self._esc(assoc.get("param_a", ""))} → {self._esc(assoc.get("param_b", ""))} · '
                f'置信度 {float(assoc.get("confidence", 0) or 0) * 100:.0f}%<br>'
                f'<span style="color:#666;">依据: {self._esc(assoc.get("scientific_basis", ""))}</span>'
                '</div>'
            )

        return f"""
  <section id="sec-mm-evidence">
    <div class="card">
      <div class="card-title"><span class="icon">🧾</span> 多模态证据</div>
      <div class="evidence-summary">{metric_html}</div>
      {"".join(figure_html)}
      {"".join(assoc_html)}
    </div>
  </section>
"""

    def _render_verify(self, data):
        results = data.get("results", {})
        comparison = results.get("comparison_with_literature", []) if isinstance(results, dict) else []
        execution_log = results.get("execution_log", {}) if isinstance(results, dict) else {}
        execution_html = self._render_execution_log(execution_log)

        if not comparison:
            # 无文献对比数据时，展示推导过程
            derivation = results.get("derivation", "") if isinstance(results, dict) else ""
            if not derivation:
                return """
  <section id="sec-verify">
    <div class="card">
      <div class="card-title"><span class="icon">✅</span> 结果验证</div>
      <p style="color:var(--text-sec);">暂无验证数据</p>
    </div>
  </section>
"""
            return f"""
  <section id="sec-verify">
    <div class="card">
      <div class="card-title"><span class="icon">✅</span> 结果验证</div>
      <div style="padding:12px;background:#fff8e1;border-radius:8px;border-left:3px solid #ff9800;font-size:13px;">
        <b>公式推导：</b><br>{self._esc(derivation).replace(chr(10), '<br>')}
      </div>
      {execution_html}
    </div>
  </section>
"""

        # 有对比数据 → 表格 + 柱状图
        table_rows = []
        bar_metrics = []
        bar_predicted = []
        bar_literature = []

        for cl in comparison:
            if not isinstance(cl, dict):
                continue
            metric = self._esc(str(cl.get("metric", cl.get("parameter", ""))))
            predicted = self._esc(str(cl.get("predicted", cl.get("calculated_value", ""))))
            literature = self._esc(str(cl.get("literature", cl.get("literature_value", ""))))
            deviation = str(cl.get("deviation", cl.get("deviation_pct", "")))
            dev_pct = self._parse_deviation(deviation)
            diff_class = "diff-high" if dev_pct > 30 else ("diff-mid" if dev_pct > 10 else "diff-low")
            status = '<span class="tag tag-red">偏差大</span>' if dev_pct > 30 else '<span class="tag tag-green">通过</span>'

            table_rows.append(
                f"<tr><td>{metric}</td><td>{predicted}</td><td>{literature}</td>"
                f'<td class="{diff_class}">{deviation}</td><td>{status}</td></tr>'
            )
            predicted_number = self._coerce_chart_number(cl.get("predicted", cl.get("calculated_value", "")))
            literature_number = self._coerce_chart_number(cl.get("literature", cl.get("literature_value", "")))
            if predicted_number is not None and literature_number is not None:
                bar_metrics.append(metric)
                bar_predicted.append(predicted_number)
                bar_literature.append(literature_number)

        # 偏差预警
        alerts = [cl for cl in comparison if isinstance(cl, dict) and self._parse_deviation(str(cl.get("deviation",""))) > 30]
        alert_html = ""
        if alerts:
            alert_items = ", ".join(self._esc(str(a.get("metric",""))) for a in alerts)
            alert_html = (
                f'<div style="margin-top:16px;padding:12px;background:#fff1f0;border-radius:8px;'
                f'font-size:13px;border-left:3px solid var(--danger);">'
                f'<b>⚠️ 偏差预警：</b>{alert_items} 偏差超过 30% 阈值，需进一步验证。</div>'
            )

        # 存储 ECharts 数据
        self._bar_data = {"metrics": bar_metrics, "predicted": bar_predicted, "literature": bar_literature}
        chart_html = (
            '<div id="barChart" style="width:100%;height:280px;"></div>'
            if bar_metrics
            else '<div style="padding:16px;color:var(--text-sec);font-size:12px;">没有可绘制的数值对比图表；非数值验证项已保留在左侧表格。</div>'
        )

        return f"""
  <section id="sec-verify">
    <div class="card">
      <div class="card-title"><span class="icon">✅</span> 结果验证</div>
      <div style="display:flex;gap:24px;flex-wrap:wrap;">
        <div style="flex:2;min-width:360px;">
          <table class="verify-table">
            <thead><tr><th>验证指标</th><th>预测值</th><th>文献值</th><th>偏差</th><th>状态</th></tr></thead>
            <tbody>{"".join(table_rows)}</tbody>
          </table>
        </div>
        <div style="flex:1;min-width:280px;">
          {chart_html}
        </div>
      </div>
      {alert_html}
      {execution_html}
    </div>
  </section>
"""

    def _render_execution_log(self, execution_log):
        if not isinstance(execution_log, dict) or not execution_log:
            return ""
        checks = execution_log.get("checks", []) if isinstance(execution_log.get("checks", []), list) else []
        check_items = []
        for check in checks[:8]:
            if not isinstance(check, dict):
                continue
            label = check.get("name", check.get("type", "check"))
            status = check.get("status", "")
            extra = []
            if check.get("equation"):
                extra.append(str(check.get("equation")))
            if check.get("r_squared") is not None:
                extra.append(f"R²={check.get('r_squared')}")
            if check.get("baseline_count") is not None:
                extra.append(f"baselines={check.get('baseline_count')}")
            if check.get("metric_count") is not None:
                extra.append(f"metrics={check.get('metric_count')}")
            check_items.append(
                f"<li><b>{self._esc(label)}</b> "
                f"<span class=\"tag tag-blue\">{self._esc(status)}</span> "
                f"{self._esc(' · '.join(extra))}</li>"
            )
        return (
            '<div class="execution-log">'
            f'<b>代码执行日志:</b> {self._esc(execution_log.get("validator", ""))} '
            f'(seed={self._esc(execution_log.get("random_seed", ""))})'
            f'<ul>{"".join(check_items)}</ul></div>'
        )

    def _render_experiments(self, data):
        exp = data.get("experiments", {})
        if not isinstance(exp, dict):
            exp = {}
        design = str(exp.get("design", ""))
        baselines = exp.get("baselines", [])
        metrics = exp.get("metrics", [])

        if not design and not baselines and not metrics:
            # 尝试从 methods 中提取
            methods = str(data.get("methods", ""))
            if not methods:
                return """
  <section id="sec-experiment">
    <div class="card">
      <div class="card-title"><span class="icon">🔬</span> 实验设计</div>
      <p style="color:var(--text-sec);">暂无实验设计数据</p>
    </div>
  </section>
"""
            return f"""
  <section id="sec-experiment">
    <div class="card">
      <div class="card-title"><span class="icon">🔬</span> 实验方法</div>
      <div style="font-size:13px;line-height:1.8;">{self._render_text_with_citations(methods).replace(chr(10), '<br>')}</div>
    </div>
  </section>
"""

        cards = []
        if design:
            cards.append(
                f'<div class="experiment-card"><div class="exp-title">🧪 实验设计</div>'
                f'<div class="exp-desc">{self._render_text_with_citations(design).replace(chr(10), "<br>")}</div></div>'
            )
        if baselines:
            bl_str = ", ".join(self._esc(str(b)) for b in baselines)
            cards.append(
                f'<div class="experiment-card"><div class="exp-title">📏 基线方法</div>'
                f'<div class="exp-desc">{bl_str}</div></div>'
            )
        if metrics:
            mt_str = ", ".join(self._esc(str(m)) for m in metrics)
            cards.append(
                f'<div class="experiment-card"><div class="exp-title">📊 评估指标</div>'
                f'<div class="exp-desc">{mt_str}</div></div>'
            )

        return f"""
  <section id="sec-experiment">
    <div class="card">
      <div class="card-title"><span class="icon">🔬</span> 实验设计</div>
      <div class="experiment-grid">{"".join(cards)}</div>
    </div>
  </section>
"""

    def _render_refs(self, data):
        refs = data.get("references", [])
        source_docs = self._source_documents_from_data(data, refs)
        source_docs_html = self._render_source_documents(source_docs)
        evidence_html = self._render_scientific_evidence_context(data)
        evidence_html += self._render_evidence_ledger(data)
        evidence_html += self._render_literature_search_diagnostics(data)
        reference_status = str(data.get("reference_status", "") or "")
        status_html = ""
        if reference_status:
            status_html = (
                '<div style="padding:12px 14px;background:#fff7e6;border:1px solid #ffd591;'
                'border-radius:8px;margin-bottom:12px;font-size:12px;color:#5f3b00;">'
                f'<b>引用状态:</b> {self._esc(reference_status)}'
                '</div>'
            )
        if not refs:
            return f"""
  <section id="sec-ref">
    <div class="card">
      <div class="card-title"><span class="icon">📚</span> 参考论文（References）</div>
      {source_docs_html}
      {evidence_html}
      {status_html}
      <div style="padding:16px;background:#fff1f0;border-radius:8px;border-left:3px solid #f5222d;">
        <p style="color:#f5222d;font-weight:500;margin:0;">⚠️ 暂无参考文献</p>
        <p style="color:#666;font-size:13px;margin-top:8px;">
          References 字段已保留，但当前没有通过白名单或证据准入的可信文献。
          请补充用户授权 PDF、DOI、摘要、公开元数据或实验记录后重新生成，系统不会为了凑数编造论文。</p>
      </div>
    </div>
  </section>
"""

        ref_items = []
        for i, ref in enumerate(refs, 1):
            if isinstance(ref, dict):
                authors = self._esc(str(ref.get("authors", "")))
                rtitle = self._esc(str(ref.get("title", "")))
                journal = self._esc(str(ref.get("journal", "")))
                year = str(ref.get("year", ""))
                doi = self._esc(str(ref.get("doi", "")))
                fidelity = str(ref.get("fidelity", ""))
                source_platform = str(ref.get("source_platform", ""))
                is_user_doc = ref.get("is_user_document", False)
                is_oa = ref.get("is_open_access", False)
                access_status = str(ref.get("access_status", "") or "")
                needs_fulltext = bool(ref.get("needs_fulltext", False))
                ref_warning = str(ref.get("warning", "") or "")
                url = str(ref.get("url", ""))
                search_text = f"{authors} {rtitle} {doi}".lower()
                gate = ref.get("evidence_gate", {}) if isinstance(ref.get("evidence_gate", {}), dict) else {}
                gate_score = gate.get("relevance_score", "")
                support_ids = gate.get("supporting_claim_ids", [])

                # 来源标签
                source_label = ""
                if is_user_doc or source_platform == "user_imported":
                    source_label = ' <span style="background:#e6f7ff;color:#1890ff;padding:1px 6px;border-radius:3px;font-size:11px;">用户导入</span>'
                elif source_platform in ("arxiv", "crossref", "semantic_scholar", "doaj", "pmc"):
                    source_label = f' <span style="background:#f9f0ff;color:#722ed1;padding:1px 6px;border-radius:3px;font-size:11px;">{source_platform}</span>'
                    if is_oa:
                        source_label += ' <span style="background:#f6ffed;color:#52c41a;padding:1px 6px;border-radius:3px;font-size:11px;">OA</span>'
                    if access_status == "metadata_only" or needs_fulltext:
                        source_label += ' <span style="background:#fff7e6;color:#ad6800;padding:1px 6px;border-radius:3px;font-size:11px;">metadata-only</span>'
                else:
                    source_label = ' <span style="background:#fff7e6;color:#fa8c16;padding:1px 6px;border-radius:3px;font-size:11px;">AI分析</span>'

                fid_html = ""
                if fidelity:
                    fid_c = {"高": "#27ae60", "中": "#fa8c16", "低": "#f5222d"}.get(fidelity, "#666")
                    fid_bg = {"高": "#e8f5e9", "中": "#fff7e6", "低": "#fff1f0"}.get(fidelity, "#f5f5f5")
                    fid_html = f' · <span class="ref-fidelity" style="background:{fid_bg};color:{fid_c};">{fidelity}保真</span>'

                doi_html = f'DOI: <a href="https://doi.org/{doi}" target="_blank" rel="noopener noreferrer" style="color:#1890ff;text-decoration:none;">{doi}</a>' if doi else ''
                url_html = f'<a href="{self._esc(url)}" target="_blank" rel="noopener noreferrer" style="color:#1890ff;text-decoration:none;">原文链接</a>' if url else ''
                support_html = ""
                if support_ids:
                    support_html = "支撑陈述: " + ", ".join(self._esc(str(cid)) for cid in support_ids[:5])
                score_html = f"证据相关度: {gate_score}" if gate_score != "" else ""
                warning_html = self._esc(ref_warning) if ref_warning else ""
                meta_parts = [p for p in [doi_html, url_html, score_html, support_html, warning_html] if p]
                meta_str = f' · '.join(meta_parts) + fid_html if meta_parts or fid_html else ''

                # 标题链接：优先 url，其次 doi
                title_href = ""
                if url:
                    title_href = self._esc(url)
                elif doi:
                    title_href = f"https://doi.org/{doi}"

                title_link_start = f'<a href="{title_href}" target="_blank" rel="noopener noreferrer" style="color:#333;text-decoration:none;">' if title_href else ''
                title_link_end = '</a>' if title_href else ''

                ref_items.append(
                    f'<div class="ref-item" id="ref-{i}" data-search="{self._esc(search_text)}">'
                    f'<div class="ref-title">[{i}] {authors}, {title_link_start}"{rtitle}"{title_link_end}, <i>{journal}</i> ({year}){source_label}</div>'
                    f'<div class="ref-meta">{meta_str}</div></div>'
                )
            else:
                ref_items.append(
                    f'<div class="ref-item" id="ref-{i}" data-search="{self._esc(str(ref).lower())}">'
                    f'<div class="ref-title">[{i}] {self._esc(str(ref))}</div></div>'
                )

        return f"""
  <section id="sec-ref">
    <div class="card">
      <div class="card-title"><span class="icon">📚</span> 参考论文（References）（{len(refs)} 篇）</div>
      {source_docs_html}
      {evidence_html}
      {status_html}
      <input class="ref-search" type="text" placeholder="🔍 搜索参考文献（标题/作者/DOI）..." oninput="filterRefs(this.value)">
      <div id="refList">{"".join(ref_items)}</div>
    </div>
  </section>
"""

    def _source_documents_from_data(self, data, refs):
        raw_docs = data.get("_source_documents") or data.get("source_documents") or []
        docs = []
        seen = set()

        def add_doc(doc):
            if not isinstance(doc, dict):
                return
            title = str(doc.get("title", "") or doc.get("paper_title", "") or "").strip()
            doc_id = doc.get("document_id") or doc.get("doc_id") or doc.get("id") or ""
            source_id = str(doc.get("source_id", "") or "").strip()
            key = (str(doc_id), title.lower()) if doc_id else ("", title.lower())
            if not title and not doc_id:
                return
            if key in seen:
                return
            seen.add(key)
            docs.append(
                {
                    "source_id": source_id,
                    "document_id": doc_id,
                    "title": title or f"用户文献 {len(docs) + 1}",
                    "source_type": doc.get("source_type", "") or doc.get("journal", ""),
                    "included_chars": doc.get("included_chars", ""),
                    "truncated": bool(doc.get("truncated", False)),
                }
            )

        for doc in raw_docs if isinstance(raw_docs, list) else []:
            add_doc(doc)

        for ref in refs or []:
            if not isinstance(ref, dict):
                continue
            if ref.get("is_user_document") or ref.get("source_platform") == "user_imported":
                add_doc(ref)

        return docs

    def _render_literature_search_diagnostics(self, data):
        diagnostics = data.get("_literature_search_diagnostics", {})
        reference_sources = data.get("_reference_sources", {})
        if not isinstance(diagnostics, dict):
            diagnostics = {}
        if not isinstance(reference_sources, dict):
            reference_sources = {}

        warnings = []
        if isinstance(diagnostics.get("warnings"), list):
            warnings.extend(str(w) for w in diagnostics.get("warnings", []) if str(w).strip())
        if isinstance(reference_sources.get("warnings"), list):
            warnings.extend(str(w) for w in reference_sources.get("warnings", []) if str(w).strip())
        warnings = list(dict.fromkeys(warnings))

        rows = []
        for phase_key, phase_label in [("pre_search", "预搜索"), ("post_search", "后搜索")]:
            phase = diagnostics.get(phase_key, {})
            if not isinstance(phase, dict):
                continue
            platform_status = phase.get("platform_status", {})
            if not isinstance(platform_status, dict):
                continue
            for platform, info in platform_status.items():
                if not isinstance(info, dict):
                    continue
                status = str(info.get("status", "unknown"))
                count = info.get("count", 0)
                reason = info.get("reason", "")
                added = info.get("added_count", "")
                rows.append(
                    "<tr>"
                    f"<td>{self._esc(phase_label)}</td>"
                    f"<td>{self._esc(platform)}</td>"
                    f"<td>{self._esc(status)}</td>"
                    f"<td>{self._esc(count)}</td>"
                    f"<td>{self._esc(added)}</td>"
                    f"<td>{self._esc(reason)}</td>"
                    "</tr>"
                )

        if not rows and not warnings:
            return ""

        warning_html = ""
        if warnings:
            warning_html = (
                '<div style="margin-top:8px;color:#92400e;font-size:12px;">'
                '<b>检索提示:</b> '
                + self._esc("; ".join(warnings[:4]))
                + "</div>"
            )

        table_html = ""
        if rows:
            table_html = (
                '<table class="evidence-table"><thead><tr>'
                '<th>阶段</th><th>平台</th><th>状态</th><th>返回</th><th>入选</th><th>原因</th>'
                '</tr></thead><tbody>'
                + "".join(rows)
                + '</tbody></table>'
            )

        return f"""
      <div style="padding:14px;background:#fffdf5;border:1px solid #fde68a;border-radius:8px;margin-bottom:16px;">
        <div style="font-weight:600;font-size:13px;margin-bottom:8px;color:#1f2937;">文献检索诊断</div>
        {table_html}
        {warning_html}
      </div>
"""

    def _render_source_documents(self, source_docs):
        if not source_docs:
            return ""

        items = []
        for idx, doc in enumerate(source_docs, 1):
            source_id = doc.get("source_id") or f"D{idx}"
            title = self._esc(doc.get("title", "未命名用户文献"))
            doc_id = doc.get("document_id", "")
            source_type = self._esc(doc.get("source_type", "") or "user_input")
            included_chars = doc.get("included_chars", "")
            truncated = doc.get("truncated", False)
            meta = [f"source={self._esc(source_id)}", f"type={source_type}"]
            if doc_id not in ("", None):
                meta.append(f"document_id={self._esc(doc_id)}")
            if included_chars not in ("", None):
                meta.append(f"included_chars={self._esc(included_chars)}")
            if truncated:
                meta.append("已截断")
            search_text = self._esc(f"{source_id} {title} {source_type} {doc_id}".lower())
            items.append(
                f'<div class="ref-item source-doc-item" data-search="{search_text}">'
                f'<div class="ref-title">[{self._esc(source_id)}] {title} '
                f'<span style="background:#e6f7ff;color:#1890ff;padding:1px 6px;'
                f'border-radius:3px;font-size:11px;">用户主动输入</span></div>'
                f'<div class="ref-meta">{" · ".join(meta)}</div></div>'
            )

        return (
            '<div class="source-doc-block">'
            '<div class="source-doc-heading">用户主动输入文献（全部显示）</div>'
            '<div class="source-doc-list">'
            + "".join(items)
            + '</div>'
            '<p class="source-doc-note">这些条目来自本次用户主动选择或输入的文献，用于溯源展示；'
            '下方 References 为系统筛选、合并或证据准入后的参考论文列表。</p>'
            '</div>'
        )

    def _render_evidence_ledger(self, data):
        ledger = data.get("_evidence_ledger", {})
        if not isinstance(ledger, dict) or not ledger:
            return ""

        summary = ledger.get("summary", {}) if isinstance(ledger.get("summary", {}), dict) else {}
        claims = ledger.get("claims", []) if isinstance(ledger.get("claims", []), list) else []
        rejected = ledger.get("rejected_references", []) if isinstance(ledger.get("rejected_references", []), list) else []

        metrics = [
            ("初始引用", summary.get("initial_reference_count", 0)),
            ("准入引用", summary.get("accepted_reference_count", 0)),
            ("剔除引用", summary.get("rejected_reference_count", 0)),
            ("已支撑陈述", f"{summary.get('supported_claim_count', 0)}/{summary.get('claim_count', 0)}"),
        ]
        metric_html = "".join(
            f'<div class="evidence-metric"><div class="num">{self._esc(value)}</div>'
            f'<div class="label">{self._esc(label)}</div></div>'
            for label, value in metrics
        )

        claim_rows = []
        for c in claims[:8]:
            if not isinstance(c, dict):
                continue
            refs = c.get("supporting_refs", [])
            status = c.get("status", "")
            status_html = (
                '<span class="tag tag-green">已支撑</span>'
                if status == "supported"
                else '<span class="tag tag-orange">待补证</span>'
            )
            ref_html = ", ".join(f'<a href="#ref-{int(r)}" class="cite-link">[{int(r)}]</a>' for r in refs if str(r).isdigit())
            claim_rows.append(
                f"<tr><td>{self._esc(c.get('claim_id', ''))}</td>"
                f"<td>{self._esc(c.get('field', ''))}</td>"
                f"<td>{self._esc(c.get('text', ''))[:220]}</td>"
                f"<td>{ref_html or '-'}</td><td>{status_html}</td></tr>"
            )

        rejected_html = ""
        if rejected:
            rej_items = []
            for r in rejected[:5]:
                if isinstance(r, dict):
                    title = self._esc(str(r.get("title", "未命名引用"))[:120])
                    score = self._esc(str(r.get("best_score", "")))
                    reason = self._esc(str(r.get("reason", "")))
                    rej_items.append(f"<li>{title} <span style='color:#999;'>score={score}; {reason}</span></li>")
            rejected_html = (
                '<details style="margin-top:10px;font-size:12px;color:#666;">'
                '<summary style="cursor:pointer;color:#fa8c16;">查看被证据准入剔除的弱相关引用</summary>'
                f'<ul style="margin-top:8px;">{"".join(rej_items)}</ul></details>'
            )

        table_html = ""
        if claim_rows:
            table_html = (
                '<table class="evidence-table"><thead><tr>'
                '<th>ID</th><th>字段</th><th>关键陈述</th><th>引用</th><th>状态</th>'
                '</tr></thead><tbody>'
                + "".join(claim_rows)
                + '</tbody></table>'
            )

        return f"""
      <div style="padding:14px;background:#f8fbff;border:1px solid #d6e8ff;border-radius:8px;margin-bottom:16px;">
        <div style="font-weight:600;font-size:13px;margin-bottom:10px;color:#1f2937;">证据账本与引用准入</div>
        <div class="evidence-summary">{metric_html}</div>
        {table_html}
        {rejected_html}
      </div>
"""

    def _render_scientific_evidence_context(self, data):
        evidence = data.get("_scientific_evidence_context", {})
        if not isinstance(evidence, dict) or not evidence:
            return ""

        source = self._esc(evidence.get("source", "未知"))
        status = self._esc(evidence.get("status", "未知"))
        question = self._esc(evidence.get("question", ""))
        answer = self._esc(evidence.get("answer", ""))
        citations = evidence.get("citations", []) if isinstance(evidence.get("citations", []), list) else []
        warnings = evidence.get("warnings", []) if isinstance(evidence.get("warnings", []), list) else []

        citation_items = []
        for idx, citation in enumerate(citations[:10], 1):
            if isinstance(citation, dict):
                title = self._esc(citation.get("title") or citation.get("source_title") or citation.get("source") or "未知来源")
                locator = self._esc(citation.get("locator") or citation.get("chunk_id") or citation.get("path") or "")
                citation_items.append(f"<li>[{idx}] {title} <span style='color:#6b7280;'>{locator}</span></li>")
            else:
                citation_items.append(f"<li>[{idx}] {self._esc(str(citation)[:240])}</li>")
        citations_html = ""
        if citation_items:
            citations_html = (
                '<div style="margin-top:10px;font-size:12px;color:#374151;">'
                '<b>证据锚点</b><ul style="margin:6px 0 0 18px;">'
                + "".join(citation_items)
                + "</ul></div>"
            )

        warnings_html = ""
        if warnings:
            warnings_html = (
                '<div style="margin-top:8px;color:#b45309;font-size:12px;">提示: '
                + self._esc("; ".join(str(w) for w in warnings[:3]))
                + "</div>"
            )

        return f"""
      <div style="padding:14px;background:#f8fbff;border:1px solid #d6e8ff;border-radius:8px;margin-bottom:16px;">
        <div style="font-weight:600;font-size:13px;margin-bottom:8px;color:#1f2937;">科学证据 RAG</div>
        <div style="font-size:12px;color:#4b5563;">来源：{source} · 状态：{status}</div>
        {f'<div style="margin-top:8px;font-size:12px;color:#4b5563;">问题：{question}</div>' if question else ''}
        {f'<div style="margin-top:8px;line-height:1.7;color:#374151;">{answer}</div>' if answer else ''}
        {citations_html}
        {warnings_html}
      </div>
"""

    def _render_conclusion(self, data):
        results = data.get("results", {})
        feas_conclusion = str(results.get("feasibility_conclusion", "")) if isinstance(results, dict) else ""
        rationale = str(data.get("rationale", ""))

        text = feas_conclusion or rationale or "暂无结论"
        next_steps = str(data.get("_next_steps", ""))

        # LLM 增强的 next_steps
        if self._enhanced and self._enhanced.get("next_steps"):
            next_steps = self._enhanced["next_steps"]

        next_html = ""
        if next_steps:
            next_html = (
                f'<div style="margin-top:12px;font-size:13px;">'
                f'<b>🔮 下一步方向：</b>{self._esc(next_steps)}</div>'
            )

        return f"""
  <section id="sec-conclusion">
    <div class="conclusion-box">
      <h4>📝 推理结论</h4>
      <p style="font-size:14px;line-height:1.8;">{self._render_text_with_citations(text).replace(chr(10), '<br>')}</p>
      {next_html}
    </div>
  </section>
"""

    # ── ECharts 图表 ─────────────────────────────────────

    def _render_charts_js(self, data, conf_pct):
        confidence = data.get("confidence", 5)
        results = data.get("results", {})
        comparison = results.get("comparison_with_literature", []) if isinstance(results, dict) else []

        # 雷达图：5维评估（从数据推断或默认）
        radar_values = self._infer_radar(data)

        # 柱状图数据
        bar_js = ""
        if hasattr(self, "_bar_data") and self._bar_data.get("metrics"):
            bd = self._bar_data
            m_str = json.dumps(bd["metrics"], ensure_ascii=False)
            p_str = json.dumps(bd["predicted"])
            l_str = json.dumps(bd["literature"])
            bar_js = f"""
var barEl = document.getElementById('barChart');
var barChart = barEl ? echarts.init(barEl) : null;
if (barChart) barChart.setOption({{
  tooltip:{{trigger:'axis'}},
  legend:{{data:['预测值','文献值'],top:0,textStyle:{{fontSize:11}}}},
  grid:{{top:30,bottom:30,left:50,right:10}},
  xAxis:{{type:'category',data:{m_str},axisLabel:{{fontSize:10}}}},
  yAxis:{{type:'value',axisLabel:{{fontSize:10}}}},
  series:[
    {{name:'预测值',type:'bar',data:{p_str},itemStyle:{{color:'#1890ff'}},barWidth:'30%'}},
    {{name:'文献值',type:'bar',data:{l_str},itemStyle:{{color:'#722ed1'}},barWidth:'30%'}}
  ]
}});
"""

        score_js = self._render_score_charts_js(data)

        return f"""
<script>
var radarEl = document.getElementById('radarChart');
var radarChart = radarEl ? echarts.init(radarEl) : null;
if (radarChart) radarChart.setOption({{
  radar:{{
    indicator:[
      {{name:'证据充分',max:100}},
      {{name:'逻辑严密',max:100}},
      {{name:'可验证性',max:100}},
      {{name:'创新性',max:100}},
      {{name:'可行性',max:100}}
    ],
    shape:'circle',splitNumber:4,
    axisName:{{color:'#666',fontSize:11}},
    splitArea:{{areaStyle:{{color:['rgba(24,144,255,0.02)','rgba(24,144,255,0.04)','rgba(24,144,255,0.06)','rgba(24,144,255,0.08)']}}}}
  }},
  series:[{{
    type:'radar',
    data:[{{
      value:{json.dumps(radar_values)},
      areaStyle:{{color:'rgba(24,144,255,0.15)'}},
      lineStyle:{{color:'#1890ff',width:2}},
      itemStyle:{{color:'#1890ff'}}
    }}]
  }}]
}});
{bar_js}
{score_js}
window.addEventListener('resize',function(){{if(radarChart)radarChart.resize();{('if(barChart)barChart.resize();' if bar_js else '')}if(window.scoreConfidenceChart)window.scoreConfidenceChart.resize();if(window.scoreFeasibilityChart)window.scoreFeasibilityChart.resize();}});
</script>
"""

    # ── LLM 增强 ─────────────────────────────────────────

    def _render_score_charts_js(self, data: Dict[str, Any]) -> str:
        breakdown = self._normalize_score_breakdown(data)
        confidence = self._score_chart_payload(breakdown.get("confidence", {}))
        feasibility = self._score_chart_payload(breakdown.get("feasibility", {}))
        if not confidence["labels"] and not feasibility["labels"]:
            return ""
        conf_json = json.dumps(confidence, ensure_ascii=False)
        feas_json = json.dumps(feasibility, ensure_ascii=False)
        return f"""
function renderScoreBreakdownChart(elId, payload, color) {{
  var el = document.getElementById(elId);
  if (!el || !payload || !payload.labels || !payload.labels.length) return null;
  var chart = echarts.init(el);
  chart.setOption({{
    tooltip:{{trigger:'axis'}},
    radar:{{
      indicator: payload.labels.map(function(label, idx){{return {{name: label, max: payload.maxValues[idx] || 100}};}}),
      radius:'58%',
      axisName:{{fontSize:10,color:'#4b5563'}},
      splitNumber:4
    }},
    grid:{{left:40,right:16,top:24,bottom:38}},
    xAxis:{{type:'category',data:payload.ids,axisLabel:{{fontSize:10,interval:0,rotate:20}}}},
    yAxis:{{type:'value',max:100,axisLabel:{{fontSize:10}}}},
    series:[
      {{type:'radar',data:[{{value:payload.values,name:'score'}}],areaStyle:{{color:color.replace('1)', '0.14)')}},lineStyle:{{color:color,width:2}},itemStyle:{{color:color}}}},
      {{type:'bar',data:payload.percentValues,itemStyle:{{color:color}},barWidth:'42%'}}
    ]
  }});
  return chart;
}}
window.scoreConfidenceChart = renderScoreBreakdownChart('scoreConfidenceChart', {conf_json}, 'rgba(24,144,255,1)');
window.scoreFeasibilityChart = renderScoreBreakdownChart('scoreFeasibilityChart', {feas_json}, 'rgba(39,174,96,1)');
"""

    @staticmethod
    def _score_chart_payload(group: Dict[str, Any]) -> Dict[str, List[Any]]:
        labels: List[str] = []
        ids: List[str] = []
        values: List[float] = []
        max_values: List[float] = []
        percent_values: List[float] = []
        dims = group.get("dimensions", []) if isinstance(group, dict) else []
        for dim in dims if isinstance(dims, list) else []:
            if not isinstance(dim, dict):
                continue
            try:
                score_num = float(dim.get("score", 0))
                max_num = float(dim.get("max_score", 100) or 100)
            except Exception:
                continue
            if max_num <= 0:
                continue
            labels.append(str(dim.get("label") or dim.get("id") or "score")[:18])
            ids.append(str(dim.get("id") or dim.get("label") or "score")[:24])
            values.append(round(score_num, 3))
            max_values.append(round(max_num, 3))
            percent_values.append(round(score_num / max_num * 100.0, 2))
        return {"labels": labels, "ids": ids, "values": values, "maxValues": max_values, "percentValues": percent_values}

    def _llm_enhance(self, data: dict) -> Optional[Dict]:
        """调用 Qwen3.7-max 生成增强内容，失败静默返回 None。"""
        try:
            api_key = self.config.api_key or os.getenv("DASHSCOPE_API_KEY")
            if not api_key:
                return None

            # 截取关键数据避免 prompt 过长
            brief = {
                "paper_title": data.get("paper_title", ""),
                "problem_statement": data.get("problem_statement", ""),
                "rationale": data.get("rationale", "")[:500],
                "confidence": data.get("confidence", 5),
                "feasibility": data.get("feasibility", ""),
            }
            chain = data.get("reasoning_chain", {})
            if isinstance(chain, dict) and chain.get("steps"):
                brief["steps_count"] = len(chain["steps"])

            prompt = (
                "你是一名科学报告编辑。基于以下假设数据，生成：\n"
                "1. 200字以内的「核心洞察」摘要（适合非专业读者）\n"
                "2. 每个推理步骤的「教学解读」（一句话解释为什么这样推理），key 为步骤编号字符串\n"
                "3. 「下一步方向」建议（1-3条具体实验方向）\n\n"
                f"假设数据：\n{json.dumps(brief, ensure_ascii=False, indent=2)}\n\n"
                '请严格以 JSON 输出：\n'
                '{"insight": "...", "teaching_notes": {"1": "...", "2": "..."}, "next_steps": "..."}'
            )

            resp = _chat(
                prompt,
                self.config,
                task="qa",  # 走 qwen3.7-plus 路由
                timeout=30,
                retries=0,
            )

            # 尝试解析 JSON
            if "```json" in resp:
                resp = resp.split("```json")[1].split("```")[0]
            elif "```" in resp:
                resp = resp.split("```")[1].split("```")[0]

            return json.loads(resp.strip())

        except Exception:
            return None

    # ── 工具方法 ─────────────────────────────────────────

    @staticmethod
    def _esc(text: str) -> str:
        """HTML 转义"""
        return html_mod.escape(str(text), quote=True)

    @staticmethod
    def _link_citations(text: str) -> str:
        """将文本中的 [1]、[2] 等引用角标转为可点击锚点链接，跳转到参考文献列表。

        例如 [1] → <a href="#ref-1" class="cite-link">[1]</a>
        仅匹配1-3位数字的角标，避免误匹配数组索引等。
        """
        import re
        def _replace_cite(m):
            num = m.group(1)
            return f'<a href="#ref-{num}" class="cite-link" title="跳转到参考文献 [{num}]">[{num}]</a>'
        return re.sub(r'\[(\d{1,3})\]', _replace_cite, text)

    def _render_text_with_citations(self, text: str) -> str:
        """先 HTML 转义，再将引用角标转为可点击链接。"""
        if text is None:
            text = ""
        escaped = html_mod.escape(str(text), quote=True)
        return self._link_citations(escaped)

    @staticmethod
    def _parse_deviation(dev_str: str) -> float:
        """从偏差字符串提取百分比数值"""
        import re
        m = re.search(r"[\d.]+", dev_str)
        if m:
            val = float(m.group())
            if "%" in dev_str:
                return val
            return val  # 假设已经是百分比
        return 0.0

    @staticmethod
    def _coerce_chart_number(value: Any) -> Optional[float]:
        text = html_mod.unescape(str(value or "")).strip()
        if not text:
            return None
        lowered = text.lower()
        if any(token in lowered for token in ["n/a", "na", "none", "unknown", "待验证", "无法识别"]):
            return None
        text = text.replace(",", "")
        text = re.split(r"±|\+/-", text, maxsplit=1)[0]
        range_match = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:-|~|–|—|to)\s*(-?\d+(?:\.\d+)?)", text)
        if range_match:
            try:
                return (float(range_match.group(1)) + float(range_match.group(2))) / 2.0
            except Exception:
                return None
        match = re.search(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", text)
        if not match:
            return None
        try:
            return float(match.group(0))
        except Exception:
            return None

    @staticmethod
    def _infer_radar(data: dict) -> List[int]:
        """从数据推断5维雷达图数值"""
        confidence = data.get("confidence", 5)
        results = data.get("results", {})
        comparison = results.get("comparison_with_literature", []) if isinstance(results, dict) else []
        refs = data.get("references", [])

        # 证据充分度：基于参考文献数量
        evidence_score = min(90, 40 + len(refs) * 10) if refs else 40

        # 逻辑严密度：基于置信度
        logic_score = int(confidence * 10)

        # 可验证性：基于对比数据量
        verify_score = min(85, 40 + len(comparison) * 10) if comparison else 35

        # 创新性：基于领域和理由长度
        rationale = str(data.get("rationale", ""))
        innovation_score = min(80, 45 + len(rationale) // 50) if rationale else 40

        # 可行性：直接映射
        feas_map = {"高": 85, "中": 60, "低": 30}
        feas_score = feas_map.get(str(data.get("feasibility", "中")), 60)

        return [evidence_score, logic_score, verify_score, innovation_score, feas_score]
