"""材料/化学领域信息提取 Prompt 模板

复制自 LitMiner，供学习平台内部使用。"""

EXTRACTION_FIELDS = """
必须提取的字段：
1. title - 文献标题
2. authors - 作者列表（逗号分隔）
3. journal - 期刊名称
4. year - 发表年份（整数）
5. doi - DOI 编号
6. abstract - 摘要
7. material_name - 研究的材料名称
8. formula - 分子式/化学式
9. synthesis_method - 合成/制备方法
10. conditions - 实验条件（JSON对象），包括但不限于：
    - temperature（温度，含单位）
    - pressure（压力，含单位）
    - time（时间，含单位）
    - atmosphere（气氛）
    - solvent（溶剂）
    - precursors（前驱体）
11. properties - 性能参数（JSON对象），包括但不限于：
    - conductivity（导电性）
    - hardness（硬度）
    - density（密度）
    - band_gap（带隙）
    - thermal_conductivity（热导率）
    - yield_strength（屈服强度）
    - 其他与材料相关的性能数据
12. characterization - 表征方法（如 XRD, SEM, TEM, XPS 等）
13. conclusions - 关键结论
14. tags - 建议的分类标签列表
"""

EXTRACTION_PROMPT = """你是一个专业的材料科学/化学领域文献信息提取助手。
你的任务是从学术文献文本中提取结构化信息，并以严格的 JSON 格式返回。

{fields}

输出要求：
1. 必须返回合法的 JSON 格式
2. 找不到的字段填空字符串 "" 或空对象 {{}}
3. conditions 和 properties 字段必须是 JSON 对象
4. tags 字段为字符串数组
5. 数值尽量包含单位
6. 不要编造不存在的信息

请以如下 JSON 格式返回：
```json
{{
    "title": "",
    "authors": "",
    "journal": "",
    "year": null,
    "doi": "",
    "abstract": "",
    "material_name": "",
    "formula": "",
    "synthesis_method": "",
    "conditions": {{
        "temperature": "",
        "pressure": "",
        "time": "",
        "atmosphere": "",
        "solvent": "",
        "precursors": ""
    }},
    "properties": {{}},
    "characterization": "",
    "conclusions": "",
    "tags": []
}}
```"""