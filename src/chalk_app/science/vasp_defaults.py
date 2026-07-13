"""
vasp_defaults.py — VASP 计算默认参数数据库与辅助工具

提供常见 DFT 计算类型的 INCAR 默认参数、POTCAR 建议、
VASPKIT 1.5.1 操作指南，以及文件格式化辅助函数。
"""

# ──────────────────────────────────────────────
# VASP 计算类型默认参数
# ──────────────────────────────────────────────
VASP_CALC_TYPES = {
    "scf": {
        "name": "Static Self-Consistent Field (SCF)",
        "incar": {
            "SYSTEM": "SCF",
            "PREC": "Accurate",
            "ENCUT": 520,
            "EDIFF": 1e-6,
            "EDIFFG": -0.01,
            "IBRION": -1,
            "ISIF": 2,
            "NSW": 0,
            "ISMEAR": 0,
            "SIGMA": 0.05,
            "GGA": "PE",
            "LREAL": "Auto",
            "ALGO": "Normal",
            "LCHARG": True,
            "LWAVE": False,
        },
        "kpoints_hint": "6x6x6 (Gamma-centered Monkhorst-Pack)",
    },
    "relax": {
        "name": "Geometry Optimization (Relaxation)",
        "incar": {
            "SYSTEM": "Relax",
            "PREC": "Accurate",
            "ENCUT": 520,
            "EDIFF": 1e-6,
            "EDIFFG": -0.02,
            "IBRION": 2,
            "ISIF": 3,
            "NSW": 100,
            "ISMEAR": 0,
            "SIGMA": 0.05,
            "GGA": "PE",
            "LREAL": "Auto",
            "ALGO": "Normal",
            "PSTRESS": 0,
            "LCHARG": True,
            "LWAVE": False,
        },
        "kpoints_hint": "6x6x6 (Gamma-centered Monkhorst-Pack)",
    },
    "dos": {
        "name": "Density of States (DOS)",
        "incar": {
            "SYSTEM": "DOS",
            "PREC": "Accurate",
            "ENCUT": 520,
            "EDIFF": 1e-6,
            "EDIFFG": -0.01,
            "IBRION": -1,
            "ISIF": 2,
            "NSW": 0,
            "ISMEAR": -5,
            "SIGMA": 0.05,
            "GGA": "PE",
            "LREAL": "Auto",
            "ALGO": "Normal",
            "LORBIT": 11,
            "NEDOS": 3001,
            "LCHARG": True,
            "LWAVE": False,
        },
        "kpoints_hint": "13x13x13 (Gamma-centered, 密网格)",
    },
    "band": {
        "name": "Band Structure",
        "incar": {
            "SYSTEM": "Band",
            "PREC": "Accurate",
            "ENCUT": 520,
            "EDIFF": 1e-6,
            "EDIFFG": -0.01,
            "IBRION": -1,
            "ISIF": 2,
            "NSW": 0,
            "ICHARG": 11,
            "ISMEAR": 0,
            "SIGMA": 0.05,
            "GGA": "PE",
            "LREAL": "Auto",
            "ALGO": "Normal",
            "LORBIT": 11,
            "LCHARG": False,
            "LWAVE": False,
        },
        "kpoints_hint": "Line mode (高对称 k 路径, 由 VASPKIT 生成)",
    },
    "phonon": {
        "name": "Phonon (Supercell Force Constants)",
        "incar": {
            "SYSTEM": "Phonon",
            "PREC": "Accurate",
            "ENCUT": 600,
            "EDIFF": 1e-8,
            "EDIFFG": -0.001,
            "IBRION": -1,
            "ISIF": 2,
            "NSW": 1,
            "ISMEAR": 0,
            "SIGMA": 0.01,
            "GGA": "PE",
            "LREAL": "Auto",
            "ALGO": "Normal",
            "ADDGRID": True,
            "LCHARG": False,
            "LWAVE": False,
            "POTIM": 0.015,
        },
        "kpoints_hint": "1x1x1 (Gamma-only, 超胞力计算)",
    },
    "optics": {
        "name": "Optical Properties",
        "incar": {
            "SYSTEM": "Optics",
            "PREC": "Accurate",
            "ENCUT": 600,
            "EDIFF": 1e-6,
            "EDIFFG": -0.01,
            "IBRION": -1,
            "ISIF": 2,
            "NSW": 0,
            "ISMEAR": 0,
            "SIGMA": 0.05,
            "GGA": "PE",
            "LREAL": "Auto",
            "ALGO": "Normal",
            "ICHARG": 1,
            "LORBIT": 11,
            "NEDOS": 2001,
            "LOPTICS": True,
            "CSHIFT": 0.01,
            "LCHARG": False,
            "LWAVE": False,
        },
        "kpoints_hint": "13x13x13 (Gamma-centered, 密网格)",
    },
}

# 金属体系覆盖参数
METAL_OVERRIDES = {
    "ISMEAR": 1,      # Methfessel-Paxton
    "SIGMA": 0.2,
}

# 常见元素 POTCAR 建议后缀
POTCAR_HINTS = {
    "Li": "Li_sv",
    "Na": "Na_sv",
    "K": "K_sv",
    "Rb": "Rb_sv",
    "Cs": "Cs_sv",
    "Ca": "Ca_sv",
    "Sr": "Sr_sv",
    "Ba": "Ba_sv",
    "Sc": "Sc_sv",
    "Ti": "Ti_pv",
    "V": "V_pv",
    "Cr": "Cr_pv",
    "Mn": "Mn_pv",
    "Fe": "Fe_pv",
    "Co": "Co_pv",
    "Ni": "Ni_pv",
    "Cu": "Cu_pv",
    "Zn": "Zn",
    "Mo": "Mo_pv",
    "Ru": "Ru_pv",
    "Rh": "Rh_pv",
    "Pd": "Pd",
    "Ag": "Ag",
    "W": "W_sv",
    "Pt": "Pt",
    "Au": "Au",
    "Hf": "Hf_pv",
    "Ta": "Ta_pv",
    "La": "La",
    "Ce": "Ce",
    "Pr": "Pr",
    "Nd": "Nd",
    "Gd": "Gd",
    "Er": "Er",
    "Yb": "Yb",
    "default": "",       # 大多数元素直接使用元素名即可
    "gw_suffix": "_GW",  # 高精度计算推荐
}

# ──────────────────────────────────────────────
# VASPKIT 1.5.1 操作指南
# ──────────────────────────────────────────────
VASPKIT_GUIDE = {
    "geometry_optimization": {
        "name": "Geometry Optimization (几何优化)",
        "steps": [
            "1. 准备初始 POSCAR（晶体结构文件）",
            "2. VASPKIT → 1 (Pre-processing) → 102 (Generate KPOINTS for Relax)",
            "   选择 k-mesh 密度（如输入 6 表示 6x6x6）",
            "3. VASPKIT → 1 → 3 (Generate POTCAR)，确认元素顺序正确",
            "4. 编写/检查 INCAR：IBRION=2, ISIF=3, NSW=100, EDIFFG=-0.02",
            "5. 运行 VASP: mpirun -np N vasp_std",
            "6. 收敛判定: grep 'reached required accuracy' OUTCAR",
            "7. 可视化: VASPKIT → 2 (Post-processing) → 25 (View Structure)",
        ],
    },
    "scf": {
        "name": "Static SCF (静态自洽)",
        "steps": [
            "1. 使用优化后的 POSCAR（CONTCAR → POSCAR）",
            "2. VASPKIT → 1 → 101 (Generate KPOINTS for SCF)",
            "   选择 k-mesh 密度（如输入 6 表示 6x6x6）",
            "3. 修改 INCAR: IBRION=-1, NSW=0, ISMEAR=-5 (绝缘体) 或 ISMEAR=1 (金属)",
            "4. LCHARG=.TRUE. 以保存 CHGCAR 供后续计算使用",
            "5. 运行 VASP: mpirun -np N vasp_std",
            "6. 收敛判定: 检查 OUTCAR 中总能量是否在 EDIFF 范围内收敛",
        ],
    },
    "band_structure": {
        "name": "Band Structure (能带结构)",
        "steps": [
            "1. 完成静态 SCF 计算，保存 CHGCAR",
            "2. VASPKIT → 1 → 2 (High-symmetry K-points)",
            "   选择合适的 Bravais 晶格类型（如 FCC/BCC/HCP/Hexagonal 等）",
            "3. VASPKIT 自动生成 KPOINTS（Line 模式）",
            "4. 修改 INCAR: ICHARG=11 (读 CHGCAR), LORBIT=11",
            "5. 运行 VASP: mpirun -np N vasp_std",
            "6. 画能带: VASPKIT → 2 → 11 (Band Structure)",
            "7. 输出文件: EBS_FILE.dat, BAND_REFCOR.dat",
        ],
    },
    "dos": {
        "name": "Density of States (态密度)",
        "steps": [
            "1. 完成静态 SCF 计算",
            "2. VASPKIT → 1 → 101 生成更密的 KPOINTS（如 SCF 的 2-3 倍）",
            "3. 修改 INCAR: ISMEAR=-5, NEDOS=3001, LORBIT=11",
            "4. 运行 VASP: mpirun -np N vasp_std",
            "5. 画 DOS: VASPKIT → 2 → 15 (Total/Partial DOS)",
            "6. 输出文件: TDOS.dat, PDOS_xxx.dat",
        ],
    },
    "phonon": {
        "name": "Phonon (声子谱)",
        "steps": [
            "1. 完成几何优化，使用 CONTCAR 作为初始结构",
            "2. VASPKIT → 1 → 201 (Supercell)，输入超胞尺寸（如 2 2 2）",
            "3. VASPKIT → 1 → 203 (Displacement)，生成带位移的 POSCAR 系列文件",
            "4. 对每个 POSCAR 分别运行 VASP 力计算（INCAR: NSW=1, IBRION=-1, EDIFF=1e-8）",
            "5. 收集所有 FORCES（vasprun.xml 或 OUTCAR）",
            "6. 后处理: VASPKIT → 2 → 23 (Phonopy) 或使用 Phonopy 命令行",
            "   phonopy -d --dim='2 2 2' && phonopy -f disp-001/vasprun.xml ...",
            "7. 绘图: phonopy --dos 或 phonopy -p band.conf",
        ],
    },
    "elastic": {
        "name": "Elastic Constants (弹性常数)",
        "steps": [
            "1. 完成几何优化，使用 CONTCAR 作为初始结构",
            "2. VASPKIT → 1 → 213 (Elastic Deformation)",
            "3. 选择变形类型（完整弹性张量）",
            "4. VASPKIT 自动生成多组变形 POSCAR",
            "5. 对每个变形 POSCAR 运行 VASP 静态计算",
            "6. VASPKIT → 2 → 213 (Elastic Constants) 拟合 Cij",
            "7. 输出: ELASTIC_PROPERTIES.dat（含体模量、剪切模量等）",
        ],
    },
    "bader": {
        "name": "Bader Charge Analysis (Bader 电荷分析)",
        "steps": [
            "1. 在 SCF/Relax 计算中设置 LAECHG = .TRUE.",
            "2. 运行 VASP 后会生成 AECCAR0 和 AECCAR2",
            "3. 合并电荷密度: VASPKIT → 2 → 14 (Charge Density)",
            "   或手动: cp AECCAR0 CHGCAR_sum; 逐点相加 AECCAR2",
            "4. 下载 Henkelman 组 Bader 程序",
            "5. 运行 Bader: bader CHGCAR -ref CHGCAR_sum",
            "6. 输出文件: ACF.dat (原子电荷), BCF.dat (体积电荷)",
        ],
    },
}

# ──────────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────────
def get_incar_template(calc_type: str, is_metal: bool = False) -> dict:
    """获取指定计算类型的 INCAR 默认参数副本。"""
    base = VASP_CALC_TYPES.get(calc_type, VASP_CALC_TYPES["scf"])["incar"].copy()
    if is_metal:
        base.update(METAL_OVERRIDES)
    return base


def get_calc_types() -> list:
    """返回所有计算类型 key 列表。"""
    return list(VASP_CALC_TYPES.keys())


def get_vaspkit_guide(task_key: str) -> dict:
    """获取 VASPKIT 操作指南。"""
    return VASPKIT_GUIDE.get(task_key, {})


def get_vaspkit_types() -> list:
    """返回所有 VASPKIT 任务 key 列表。"""
    return list(VASPKIT_GUIDE.keys())


def format_incar(params: dict) -> str:
    """将参数字典格式化为 INCAR 文件内容。"""
    lines = [
        "! Generated by Chalk - Computational Modeling Module",
        "! https://github.com/example/chalk",
        "",
    ]
    for key, val in params.items():
        if isinstance(val, bool):
            lines.append(f"{key} = {'.TRUE.' if val else '.FALSE.'}")
        elif isinstance(val, float):
            # 科学计数法或普通浮点
            lines.append(f"{key} = {val}")
        elif isinstance(val, int):
            lines.append(f"{key} = {val}")
        else:
            lines.append(f"{key} = {val}")
    return "\n".join(lines) + "\n"


def format_kpoints(mesh: str = "6 6 6", mode: str = "Gamma") -> str:
    """格式化 KPOINTS 文件内容。"""
    lines = [
        "K-points generated by Chalk",
        "0",
        mode,
        mesh,
        "0 0 0",
    ]
    return "\n".join(lines) + "\n"


def format_kpoints_line(path_desc: str = "") -> str:
    """格式化 Line 模式 KPOINTS 模板（能带计算）。"""
    lines = [
        "K-points for band structure (generated by Chalk)",
        "0",
        "Line-mode",
        "reciprocal",
        "",
        path_desc or "# 请使用 VASPKIT → 1 → 2 自动生成高对称 k 路径",
        "# 或手动指定，例如:",
        "# 0.0 0.0 0.0   GAMMA",
        "# 0.5 0.0 0.5   X",
        "# 0.5 0.5 0.5   L",
        "# 0.0 0.0 0.0   GAMMA",
    ]
    return "\n".join(lines) + "\n"


def get_potcar_suffix(element: str) -> str:
    """根据元素返回推荐的 POTCAR 文件名后缀。"""
    return POTCAR_HINTS.get(element, POTCAR_HINTS["default"])
