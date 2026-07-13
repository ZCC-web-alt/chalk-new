"""
chem_structure.py — 化学品结构式与基本信息查询

通过 PubChem REST API 获取：
- 2D 结构图 (PNG)
- 基本信息：CAS、分子式、分子量、IUPAC名、同义词
- 物理化学性质：熔点、沸点、密度、溶解度等
- 无需安装 RDKit，纯 HTTP 调用，轻量无依赖

PubChem API 文档：https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest
"""

from __future__ import annotations
import json
import re
from typing import Optional, Dict, List

import requests

PUBCHEM_BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"

# 常见单质元素名称（查询时过滤掉）
ELEMENTAL_NAMES = {
    "hydrogen", "oxygen", "nitrogen", "chlorine", "carbon",
    "sulfur", "phosphorus", "fluorine", "bromine", "iodine",
    "helium", "neon", "argon", "xenon", "krypton",
    "lithium", "sodium", "potassium", "rubidium", "cesium",
    "beryllium", "magnesium", "calcium", "strontium", "barium",
    "titanium", "vanadium", "chromium", "manganese", "iron",
    "cobalt", "nickel", "copper", "zinc", "aluminum",
    "silver", "gold", "platinum", "palladium", "mercury",
    "lead", "tin", "tungsten", "molybdenum", "silicon",
    "boron", "carbon monoxide", "carbon dioxide", "ammonia",
    "methane", "ethane", "ethylene",
}


def _pubchem_get(url: str, timeout: int = 12):
    try:
        from literature_search import rate_limit_for_platform
        rate_limit_for_platform("pubchem")
    except Exception:
        pass
    return requests.get(url, timeout=timeout)


class ChemInfo:
    """化学品信息数据类"""

    def __init__(self):
        self.name: str = ""               # 查询名
        self.cid: Optional[int] = None    # PubChem CID
        self.iupac_name: str = ""
        self.molecular_formula: str = ""
        self.molecular_weight: str = ""
        self.cas: str = ""
        self.synonyms: List[str] = []
        self.canonical_smiles: str = ""
        self.inchi: str = ""
        self.inchikey: str = ""
        # 物理性质
        self.melting_point: str = ""
        self.boiling_point: str = ""
        self.density: str = ""
        self.solubility: str = ""
        self.appearance: str = ""
        # 结构图 URL
        self.image_url: str = ""
        self.pubchem_url: str = ""

    def to_markdown(self) -> str:
        """生成 Markdown 格式的信息卡片"""
        lines = [
            f"## 🧪 {self.name}",
            "",
            f"| 属性 | 值 |",
            f"|------|-----|",
            f"| **PubChem CID** | {self.cid or '—'} |",
            f"| **IUPAC 名称** | {self.iupac_name or '—'} |",
            f"| **分子式** | {self.molecular_formula or '—'} |",
            f"| **分子量** | {self.molecular_weight or '—'} |",
            f"| **CAS 号** | {self.cas or '—'} |",
            f"| **SMILES** | `{self.canonical_smiles or '—'}` |",
            f"| **InChIKey** | `{self.inchikey or '—'}` |",
            f"| **熔点** | {self.melting_point or '—'} |",
            f"| **沸点** | {self.boiling_point or '—'} |",
            f"| **密度** | {self.density or '—'} |",
            f"| **溶解性** | {self.solubility or '—'} |",
            f"| **外观** | {self.appearance or '—'} |",
            "",
        ]
        if self.synonyms:
            lines.append(f"**同义词**：{', '.join(self.synonyms[:10])}")
            lines.append("")
        if self.pubchem_url:
            lines.append(f"🔗 [在 PubChem 中查看]({self.pubchem_url})")
        return "\n".join(lines)


def _get_cid_by_name(name: str) -> Optional[int]:
    """通过名称查询 PubChem CID"""
    try:
        url = f"{PUBCHEM_BASE}/compound/name/{name}/cids/JSON"
        resp = _pubchem_get(url, timeout=12)
        if resp.status_code != 200:
            return None
        data = resp.json()
        cids = data.get("IdentifierList", {}).get("CID", [])
        if cids:
            return int(cids[0])
        return None
    except Exception:
        return None


def _get_property(cid: int, properties: List[str]) -> Dict:
    """批量获取化合物属性"""
    try:
        props_str = ",".join(properties)
        url = f"{PUBCHEM_BASE}/compound/cid/{cid}/property/{props_str}/JSON"
        resp = _pubchem_get(url, timeout=12)
        if resp.status_code != 200:
            return {}
        data = resp.json()
        props = data.get("PropertyTable", {}).get("Properties", [])
        return props[0] if props else {}
    except Exception:
        return {}


def _get_synonyms(cid: int, limit: int = 20) -> List[str]:
    """获取化合物同义词"""
    try:
        url = f"{PUBCHEM_BASE}/compound/cid/{cid}/synonyms/JSON"
        resp = _pubchem_get(url, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
        syns = data.get("InformationList", {}).get("Information", [{}])[0].get("Synonym", [])
        return [s for s in syns[:limit] if s]
    except Exception:
        return []


def query_chem_info(name: str) -> Optional[ChemInfo]:
    """
    查询化学品的完整信息。
    返回 ChemInfo 对象，若查询失败返回 None。
    """
    # 1. 获取 CID
    cid = _get_cid_by_name(name)
    if cid is None:
        return None

    info = ChemInfo()
    info.name = name
    info.cid = cid
    info.pubchem_url = f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}"
    info.image_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/PNG?image_size=large"

    # 2. 获取基本属性
    props = _get_property(cid, [
        "IUPACName", "MolecularFormula", "MolecularWeight",
        "CanonicalSMILES", "InChI", "InChIKey",
        "IsomericSMILES", "XLogP",
    ])
    info.iupac_name = props.get("IUPACName", "")
    info.molecular_formula = props.get("MolecularFormula", "")
    info.molecular_weight = str(props.get("MolecularWeight", ""))
    info.canonical_smiles = props.get("CanonicalSMILES", "")
    info.inchi = props.get("InChI", "")
    info.inchikey = props.get("InChIKey", "")

    # 3. 获取同义词（从中提取 CAS）
    info.synonyms = _get_synonyms(cid, limit=30)
    for syn in info.synonyms:
        # CAS 格式：xxxx-xx-x
        if re.match(r"^\d{2,7}-\d{2}-\d$", syn):
            info.cas = syn
            break

    # 4. 获取物理性质（通过实验数据）
    try:
        url = f"{PUBCHEM_BASE}/compound/cid/{cid}/xrefs/SBURL/JSON"
        resp = _pubchem_get(url, timeout=10)
    except Exception:
        pass

    # 尝试从注释/描述中获取物理性质
    try:
        url = f"{PUBCHEM_BASE}/compound/cid/{cid}/description/JSON"
        resp = _pubchem_get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            descriptions = data.get("InformationList", {}).get("Information", [])
            desc_texts = []
            for d in descriptions:
                t = d.get("Description", "")
                if t:
                    desc_texts.append(t)
            full_desc = " ".join(desc_texts)

            # 简单正则提取常见物理性质
            mp_match = re.search(r"melting point.*?([-\d\.]+\s*°?C)", full_desc, re.I)
            if mp_match:
                info.melting_point = mp_match.group(1)

            bp_match = re.search(r"boiling point.*?([-\d\.]+\s*°?C)", full_desc, re.I)
            if bp_match:
                info.boiling_point = bp_match.group(1)

            den_match = re.search(r"density.*?([\d\.]+\s*g/cm3?)", full_desc, re.I)
            if den_match:
                info.density = den_match.group(1)

            sol_match = re.search(r"solubil.*?([^\.]{5,60}\.)", full_desc, re.I)
            if sol_match:
                info.solubility = sol_match.group(1).strip()[:100]

            app_match = re.search(r"appearance[^\.]{0,30}([^\.]{3,60})", full_desc, re.I)
            if app_match:
                info.appearance = app_match.group(1).strip()[:100]
    except Exception:
        pass

    return info


def fetch_structure_image(name: str) -> Optional[bytes]:
    """
    仅获取化学品的 2D 结构图 PNG 字节。
    失败返回 None。
    """
    try:
        url = f"{PUBCHEM_BASE}/compound/name/{name}/PNG?image_size=large"
        resp = _pubchem_get(url, timeout=15)
        if resp.status_code == 200 and resp.content[:4] == b"\x89PNG":
            return resp.content
        # 如果名称查询失败，尝试先查 CID
        cid = _get_cid_by_name(name)
        if cid:
            url = f"{PUBCHEM_BASE}/compound/cid/{cid}/PNG?image_size=large"
            resp = _pubchem_get(url, timeout=15)
            if resp.status_code == 200:
                return resp.content
        return None
    except Exception:
        return None


def extract_chemical_names(text: str, config=None) -> List[str]:
    """
    从文本中提取化学品名称列表（轻量级正则+启发式，不依赖 LLM）。
    作为 LLM 提取的兜底/加速方案。
    """
    names = set()
    # 常见化学品模式
    patterns = [
        # 无机物
        r"\b(?:sulfuric|hydrochloric|nitric|phosphoric|hydrofluoric|acetic|formic|citric|oxalic|benzoic)\s+acid\b",
        r"\b(?:sodium|potassium|calcium|magnesium|ammonium|lithium|copper|iron|zinc|aluminum|silver|barium|mercury)\s+(?:chloride|sulfate|nitrate|carbonate|hydroxide|acetate|phosphate|oxide)\b",
        r"\b(?:hydrogen|oxygen|nitrogen|chlorine|carbon dioxide|carbon monoxide|ammonia|methane|ethane|ethylene)\b",
        # 常见有机物
        r"\b(?:methanol|ethanol|propanol|butanol|isopropanol|ethylene glycol|glycerol)\b",
        r"\b(?:acetone|acetaldehyde|formaldehyde|benzaldehyde)\b",
        r"\b(?:ethyl acetate|methyl acetate|butyl acetate)\b",
        r"\b(?:benzene|toluene|xylene|naphthalene|phenol|aniline)\b",
        r"\b(?:tetrahydrofuran|THF|dimethylformamide|DMF|dimethyl sulfoxide|DMSO|NMP|DMAC)\b",
        r"\b(?:hexane|heptane|cyclohexane|petroleum ether|diethyl ether)\b",
        r"\b(?:dichloromethane|chloroform|carbon tetrachloride|CCl4|DCM)\b",
        r"\b(?:LiCoO2|LiFePO4|NCM|NCA|LiMn2O4|Li4Ti5O12|LiNiO2)\b",  # 锂电材料
    ]
    text_lower = text.lower()
    for pat in patterns:
        for m in re.finditer(pat, text_lower, re.I):
            name = m.group(0).strip()
            if name.lower() not in ELEMENTAL_NAMES:
                names.add(name)

    # 大写化学式模式：如 H2SO4, NaCl, C6H12O6
    formula_pat = r"\b(?:[A-Z][a-z]?\d*)+(?:[A-Z][a-z]?\d*)*\b"
    for m in re.finditer(formula_pat, text):
        word = m.group(0)
        # 简单过滤：至少包含一个数字，且长度合适
        if re.search(r"\d", word) and 3 <= len(word) <= 20:
            # 过滤单质化学式（如 O2, H2, N2, Cl2, Fe 等）
            elements = re.findall(r"[A-Z][a-z]?", word)
            if len(set(elements)) <= 1:
                continue  # 只有一种元素，是单质，跳过
            names.add(word)

    return sorted(names)


if __name__ == "__main__":
    # 简单测试
    import sys
    test_name = sys.argv[1] if len(sys.argv) > 1 else "sulfuric acid"
    print(f"查询: {test_name}")
    info = query_chem_info(test_name)
    if info:
        print(info.to_markdown())
    else:
        print("未查询到信息")
