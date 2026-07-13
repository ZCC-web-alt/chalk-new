"""
data_cleaner.py — 多模态数据智能清洗模块

对多模态大模型从科学图表/表格中提取的原始数据进行清洗，包括：
  1. 单位统一 — 能量、长度、电流密度、温度、压力等物理量的单位标准化
  2. OCR 纠错 — 常见 OCR 识别错误（字母↔数字）的自动修正
  3. 数值范围校验 — 基于已知物理化学常数范围，标记可疑数据
  4. 去重合并 — 同一参数在多处出现时保留最精确值并标注来源
  5. 格式标准化 — 统一输出格式（有效数字、带单位结构化字典）

比赛要求对应：
  "实现数据的智能清洗、分析与关联挖掘，精准识别关键信息"
"""

from __future__ import annotations

import math
import re
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# 数据类
# ─────────────────────────────────────────────────────────────

@dataclass
class CleanedDataPoint:
    """清洗后的单个数据点"""
    parameter: str          # 参数名（英文，如 "d-band center"）
    parameter_cn: str       # 参数名（中文，如 "d带中心"）
    value: float            # 数值（已转换到标准单位）
    unit: str               # 标准单位（如 "eV"）
    original_value: str     # 原始字符串（如 "1.95 eV"）
    original_unit: str      # 原始单位
    source: str             # 来源标注（如 "Fig.3 XPS"）
    is_suspicious: bool = False  # 是否标记为可疑
    suspicious_reason: str = ""  # 可疑原因
    precision: int = 3      # 有效数字位数


@dataclass
class CleaningReport:
    """清洗报告"""
    total_points: int = 0
    cleaned_points: int = 0
    unit_converted: int = 0
    ocr_fixed: int = 0
    suspicious_found: int = 0
    duplicates_merged: int = 0
    details: List[Dict] = field(default_factory=list)

    def to_html(self) -> str:
        parts = []
        color = "#27ae60" if self.suspicious_found == 0 else (
            "#f39c12" if self.suspicious_found <= 2 else "#e74c3c")
        parts.append(
            f"<h4 style='color:{color};'>数据清洗报告 "
            f"({self.cleaned_points}/{self.total_points} 通过)</h4>"
        )
        parts.append("<ul>")
        parts.append(f"<li>单位统一: {self.unit_converted} 项</li>")
        parts.append(f"<li>OCR纠错: {self.ocr_fixed} 项</li>")
        parts.append(f"<li>可疑数据: {self.suspicious_found} 项</li>")
        parts.append(f"<li>去重合并: {self.duplicates_merged} 项</li>")
        parts.append("</ul>")
        if self.details:
            parts.append("<h5>详细数据</h5><table style='width:100%;border-collapse:collapse;font-size:12px;'>")
            parts.append(
                "<tr style='background:#f8f9fa;'><th>参数</th><th>值</th>"
                "<th>单位</th><th>来源</th><th>状态</th></tr>"
            )
            for d in self.details:
                status = (
                    "<span style='color:#e74c3c;'>可疑</span>"
                    if d.get("suspicious")
                    else "<span style='color:#27ae60;'>正常</span>"
                )
                parts.append(
                    f"<tr><td>{d['parameter']}</td><td>{d['value']}</td>"
                    f"<td>{d['unit']}</td><td>{d['source']}</td><td>{status}</td></tr>"
                )
            parts.append("</table>")
        return "\n".join(parts)


# ─────────────────────────────────────────────────────────────
# 单位转换表
# ─────────────────────────────────────────────────────────────

# 标准单位体系：每种物理量定义一个标准单位，所有值转换到该单位
UNIT_CONVERSIONS: Dict[str, Dict[str, float]] = {
    # 能量
    "eV": {"meV": 1e-3, "keV": 1e3, "J": 6.242e18, "kJ/mol": 0.01036, "kcal/mol": 0.04336},
    # 长度
    "nm": {"Angstrom": 10.0, "A": 10.0, "pm": 0.001, "um": 1e3, "mm": 1e6},
    # 电流密度
    "mA/cm2": {"uA/cm2": 1e-3, "A/cm2": 1e3, "mA/m2": 1e-4, "A/m2": 0.1},
    # 温度
    "K": {"C": 273.15, "F": 255.93},  # C→K: +273.15, F→K: (F-32)*5/9+273.15
    # 压力
    "Pa": {"kPa": 1e3, "MPa": 1e6, "atm": 101325, "bar": 1e5, "mbar": 100},
    # 时间
    "s": {"ms": 1e-3, "us": 1e-6, "min": 60, "h": 3600},
    # 质量
    "mg": {"ug": 1e-3, "g": 1e3, "kg": 1e6},
}

# 单位别名映射（大小写不敏感匹配）
UNIT_ALIASES: Dict[str, str] = {
    "ev": "eV", "mev": "meV", "kev": "keV",
    "nm": "nm", "angstrom": "Angstrom", "a": "Angstrom",
    "ang": "Angstrom", "pm": "pm", "μm": "um", "mm": "mm",
    "ma/cm2": "mA/cm2", "ma/cm²": "mA/cm2", "ua/cm2": "uA/cm2",
    "a/cm2": "A/cm2", "a/cm²": "A/cm2",
    "k": "K", "c": "C", "°c": "C", "°c": "C",
    "pa": "Pa", "kpa": "kPa", "mpa": "MPa",
    "atm": "atm", "bar": "bar", "mbar": "mbar",
    "j": "J", "kj/mol": "kJ/mol", "kcal/mol": "kcal/mol",
}

# 中文单位 → 英文
CN_UNIT_MAP: Dict[str, str] = {
    "电子伏特": "eV", "毫电子伏特": "meV", "千电子伏特": "keV",
    "纳米": "nm", "埃": "Angstrom", "皮米": "pm", "微米": "um",
    "毫安每平方厘米": "mA/cm2", "安培每平方厘米": "A/cm2",
    "开尔文": "K", "摄氏度": "C", "华氏度": "F",
    "帕斯卡": "Pa", "千帕": "kPa", "兆帕": "MPa", "标准大气压": "atm",
}


# ─────────────────────────────────────────────────────────────
# 合理范围（复用 hypothesis_verifier 的逻辑）
# ─────────────────────────────────────────────────────────────

REASONABLE_RANGES: Dict[str, Tuple[float, float]] = {
    # 催化/电化学
    "activation energy": (0.1, 5.0),
    "overpotential": (0.1, 1.5),
    "onset potential": (0.3, 1.2),
    "half-wave potential": (0.5, 0.95),
    "limiting current density": (1.0, 10.0),
    "electron transfer number": (2.0, 4.02),
    "tafel slope": (20, 200),
    # 热力学
    "binding energy": (0.1, 10.0),
    "adsorption energy": (-5.0, 8.0),
    "formation energy": (-5.0, 5.0),
    "band gap": (0.0, 8.0),
    "d-band center": (-3.5, 1.0),
    "work function": (2.0, 6.5),
    # 结构
    "lattice constant": (2.0, 15.0),
    "interlayer distance": (3.0, 12.0),
    # 性能
    "faradaic efficiency": (0, 100),
    "coulombic efficiency": (50, 100),
    "specific capacity": (10, 5000),
    "conversion efficiency": (0.1, 100),
    # XPS
    "binding energy xps": (0, 1200),
    # 通用
    "potential": (-1.0, 2.0),
    "resistance": (0, 1e6),
    "capacitance": (1e-6, 1e3),
}

# 参数中文名 → 英文名（模糊匹配用）
PARAM_CN_TO_EN: Dict[str, str] = {
    "d带中心": "d-band center", "d-带中心": "d-band center",
    "过电势": "overpotential", "过电位": "overpotential",
    "起始电位": "onset potential", "半波电位": "half-wave potential",
    "塔菲尔斜率": "tafel slope", "tafel斜率": "tafel slope",
    "吸附能": "adsorption energy", "结合能": "binding energy",
    "带隙": "band gap", "功函数": "work function",
    "晶格常数": "lattice constant", "层间距": "interlayer distance",
    "法拉第效率": "faradaic efficiency",
    "库伦效率": "coulombic efficiency", "库仑效率": "coulombic efficiency",
    "比容量": "specific capacity", "转化效率": "conversion efficiency",
    "极限电流密度": "limiting current density",
    "电子转移数": "electron transfer number",
    "活化能": "activation energy", "形成能": "formation energy",
    "电阻": "resistance", "电容": "capacitance",
}


# ─────────────────────────────────────────────────────────────
# OCR 纠错规则
# ─────────────────────────────────────────────────────────────

OCR_FIXES: Dict[str, str] = {
    "O": "0", "o": "0",   # 在纯数值上下文中
    "l": "1", "I": "1",   # 在纯数值上下文中
    "S": "5",             # 在纯数值上下文（少见，保守启用）
}

# 需要跳过 OCR 纠错的上下文（这些字母不应被替换）
OCR_SKIP_WORDS = {"Co", "CO", "Ce", "Cr", "Cu", "Cd", "Ca", "Cl", "Cs", "Li",
                  "La", "Lu", "Lu", "Nd", "Ni", "Na", "Nb", "N", "O", "S",
                  "Si", "Sn", "Sr", "Sc", "Sm", "Se", "Ti", "Te", "V", "W",
                  "Zn", "Zr", "H", "He", "C", "B", "F", "P", "K", "Al", "Mg",
                  "Fe", "Mn", "Mo", "Ru", "Rh", "Pd", "Ag", "In", "Sb", "Ba",
                  "Ta", "Re", "Os", "Ir", "Pt", "Au", "Hg", "Pb", "Bi", "U",
                  "OH", "OOH", "ORR", "OER", "XPS", "XRD", "TEM", "SEM",
                  "RHE", "SCE", "vs", "RPM", "rpm", "pH", "DA", "UV"}


# ─────────────────────────────────────────────────────────────
# 核心清洗类
# ─────────────────────────────────────────────────────────────

class DataCleaner:
    """数据清洗引擎"""

    def __init__(self):
        self.report = CleaningReport()

    def clean_data_points(
        self,
        raw_points: List[Dict[str, Any]],
        source: str = "",
    ) -> List[CleanedDataPoint]:
        """
        清洗原始数据点列表。

        Args:
            raw_points: 从多模态分析提取的原始数据点，每个是 dict:
                {"parameter": str, "value": str|float, "unit": str, ...}
            source: 数据来源标注（如 "Fig.3 XPS"）

        Returns:
            清洗后的 CleanedDataPoint 列表
        """
        cleaned: List[CleanedDataPoint] = []
        self.report = CleaningReport()
        self.report.total_points = len(raw_points)

        for pt in raw_points:
            param = pt.get("parameter", "")
            value_raw = pt.get("value", "")
            unit_raw = pt.get("unit", "")
            pt_source = pt.get("source", source)

            # 解析数值
            value_str = str(value_raw).strip()
            value_str = self._fix_ocr(value_str, param)
            parsed_value, precision = self._parse_number(value_str)

            if parsed_value is None:
                logger.warning(f"无法解析数值: {value_str} (参数: {param})")
                continue

            # 标准化参数名
            param_en, param_cn = self._normalize_parameter(param)

            # 标准化单位
            std_unit = self._normalize_unit(unit_raw)

            # 单位转换
            converted_value, did_convert = self._convert_unit(
                parsed_value, unit_raw, std_unit
            )
            if did_convert:
                self.report.unit_converted += 1

            # 范围校验
            is_suspicious, reason = self._check_range(param_en, converted_value, std_unit)
            if is_suspicious:
                self.report.suspicious_found += 1

            dp = CleanedDataPoint(
                parameter=param_en,
                parameter_cn=param_cn or param,
                value=converted_value,
                unit=std_unit,
                original_value=str(value_raw),
                original_unit=unit_raw,
                source=pt_source,
                is_suspicious=is_suspicious,
                suspicious_reason=reason,
                precision=precision,
            )
            cleaned.append(dp)
            self.report.details.append({
                "parameter": f"{param_cn or param} ({param_en})",
                "value": f"{converted_value:.{precision}f}",
                "unit": std_unit,
                "source": pt_source,
                "original": f"{value_raw} {unit_raw}",
                "suspicious": is_suspicious,
                "reason": reason,
            })

        # 去重合并
        merged = self._deduplicate(cleaned)
        self.report.cleaned_points = len(merged)
        self.report.duplicates_merged = len(cleaned) - len(merged)

        return merged

    # ── OCR 纠错 ──

    def _fix_ocr(self, value_str: str, param: str) -> str:
        """在纯数值上下文中修正 OCR 错误。"""
        # 检查是否包含化学元素或专业缩写（不应纠错）
        context = param + " " + value_str
        skip = any(word in context for word in OCR_SKIP_WORDS)
        if skip:
            return value_str

        # 只在纯数值字符串（可能带小数点和负号）中纠错
        if re.fullmatch(r"[0-9OlISs\.\-+eE]+", value_str.replace(" ", "")):
            fixed = value_str
            for old, new in OCR_FIXES.items():
                fixed = fixed.replace(old, new)
            if fixed != value_str:
                self.report.ocr_fixed += 1
                logger.info(f"OCR 纠错: '{value_str}' → '{fixed}'")
            return fixed
        return value_str

    # ── 数值解析 ──

    def _parse_number(self, s: str) -> Tuple[Optional[float], int]:
        """解析字符串为 float，返回 (值, 精度)。"""
        s = s.strip().replace(",", ".")  # 1,5 → 1.5
        # 提取数值部分（可能带 ± 或 ± 符号）
        m = re.search(r"([±\+\-]?\d+\.?\d*)\s*([eE][\+\-]?\d+)?", s)
        if not m:
            return None, 0
        try:
            val = float(m.group(0))
            # 计算有效数字位数
            num_str = s.replace("±", "").replace("+", "").strip()
            num_str = re.sub(r"[eE][\+\-]?\d+", "", num_str)  # 去掉科学计数法
            if "." in num_str:
                precision = len(num_str.replace(".", "").lstrip("0"))
            else:
                precision = len(num_str.lstrip("0"))
            return val, max(precision, 1)
        except ValueError:
            return None, 0

    # ── 参数名标准化 ──

    def _normalize_parameter(self, param: str) -> Tuple[str, str]:
        """返回 (英文名, 中文名)。"""
        param_lower = param.lower().strip()
        # 先查中文映射
        for cn, en in PARAM_CN_TO_EN.items():
            if cn.lower() in param_lower or param_lower in cn.lower():
                return en, cn
        # 尝试英文模糊匹配
        for en_key in REASONABLE_RANGES:
            if en_key in param_lower or param_lower in en_key:
                return en_key, ""
        return param_lower, ""

    # ── 单位标准化 ──

    def _normalize_unit(self, unit: str) -> str:
        """将单位字符串映射到标准单位名称。"""
        if not unit or unit.strip() in ("", "-", "N/A", "—"):
            return ""
        u = unit.strip().lower()
        # 中文单位
        if u in CN_UNIT_MAP:
            return CN_UNIT_MAP[u]
        # 英文别名
        return UNIT_ALIASES.get(u, unit.strip())

    # ── 单位转换 ──

    def _convert_unit(
        self, value: float, from_unit: str, to_unit: str
    ) -> Tuple[float, bool]:
        """
        尝试将 value 从 from_unit 转换为 to_unit。
        返回 (converted_value, did_convert)。
        """
        if not from_unit or not to_unit or from_unit.lower() == to_unit.lower():
            return value, False

        from_std = UNIT_ALIASES.get(from_unit.lower(), from_unit)
        to_std = UNIT_ALIASES.get(to_unit.lower(), to_unit)

        # 在转换表中查找
        for std_unit, conversions in UNIT_CONVERSIONS.items():
            from_factor = conversions.get(from_std)
            to_factor = conversions.get(to_std)
            if from_factor is not None and to_factor is not None:
                # 先转到基本单位，再转到目标单位
                base_value = value * from_factor
                converted = base_value / to_factor
                logger.info(
                    f"单位转换: {value} {from_std} → {converted:.4f} {to_std}"
                )
                return round(converted, 4), True

        return value, False

    # ── 范围校验 ──

    def _check_range(
        self, param: str, value: float, unit: str
    ) -> Tuple[bool, str]:
        """检查数值是否在合理范围内。返回 (is_suspicious, reason)。"""
        param_lower = param.lower()

        # 温度特殊处理（K 和 C 范围不同）
        if param_lower in ("temperature", "温度") and unit == "C":
            if not (-50 <= value <= 500):
                return True, f"温度 {value} {unit} 超出常见实验范围 (-50 ~ 500 C)"

        for key, (lo, hi) in REASONABLE_RANGES.items():
            if key in param_lower or param_lower in key:
                # 考虑 10% 容差
                tol_lo = lo * 0.9 if lo > 0 else lo * 1.1
                tol_hi = hi * 1.1 if hi > 0 else hi * 0.9
                if not (tol_lo <= value <= tol_hi):
                    return (
                        True,
                        f"{param} = {value:.3f} {unit} 超出合理范围 "
                        f"({lo} ~ {hi})",
                    )
                break

        return False, ""

    # ── 去重合并 ──

    def _deduplicate(
        self, points: List[CleanedDataPoint]
    ) -> List[CleanedDataPoint]:
        """
        对同一参数名的数据点去重，保留精度最高的值。
        """
        groups: Dict[str, List[CleanedDataPoint]] = {}
        for p in points:
            key = p.parameter.lower()
            groups.setdefault(key, []).append(p)

        merged = []
        for key, group in groups.items():
            if len(group) == 1:
                merged.append(group[0])
            else:
                # 按精度降序排列，取精度最高的
                group.sort(key=lambda x: x.precision, reverse=True)
                best = group[0]
                # 在来源中标注所有出处
                all_sources = [g.source for g in group if g.source]
                if len(all_sources) > 1:
                    best.source = best.source + " (合并自: " + ", ".join(all_sources[1:]) + ")"
                merged.append(best)

        return merged


# ─────────────────────────────────────────────────────────────
# 便捷函数
# ─────────────────────────────────────────────────────────────

def clean_extracted_data(
    raw_points: List[Dict[str, Any]],
    source: str = "",
) -> Tuple[List[CleanedDataPoint], CleaningReport]:
    """
    一键清洗提取数据。

    Args:
        raw_points: 原始数据点列表，每个 dict 至少含:
            {"parameter": str, "value": str|float, "unit": str}
        source: 来源标注

    Returns:
        (cleaned_points, report)
    """
    cleaner = DataCleaner()
    result = cleaner.clean_data_points(raw_points, source)
    return result, cleaner.report
