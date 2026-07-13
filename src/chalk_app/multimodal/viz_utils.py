import os
import re
from collections import Counter
from typing import List, Tuple, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def safe_mkdir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def tokenize(text: str) -> List[str]:
    text = text.strip()
    if not text:
        return []
    try:
        import jieba

        words = [w.strip() for w in jieba.cut(text) if w.strip()]
        words = [w for w in words if len(w) >= 2]
        return words
    except Exception:
        words = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z]{2,}", text)
        return words


def top_terms(text: str, k: int = 20) -> List[Tuple[str, int]]:
    tokens = tokenize(text)
    c = Counter(tokens)
    return c.most_common(k)


def plot_top_terms(terms: List[Tuple[str, int]], out_path: str, title: str) -> Optional[str]:
    if not terms:
        return None
    labels = [t for t, _ in terms][::-1]
    values = [v for _, v in terms][::-1]

    plt.figure(figsize=(10, 6))
    plt.barh(labels, values)
    plt.title(title)
    plt.tight_layout()
    plt.savefig(out_path, dpi=160)
    plt.close()
    return out_path


def plot_hist(values: List[int], out_path: str, title: str, xlabel: str) -> Optional[str]:
    if not values:
        return None
    plt.figure(figsize=(10, 5))
    plt.hist(values, bins=min(40, max(10, int(len(values) ** 0.5))))
    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel("频数")
    plt.tight_layout()
    plt.savefig(out_path, dpi=160)
    plt.close()
    return out_path


def try_load_dataframe(path: str):
    import pandas as pd

    lower = path.lower()
    if lower.endswith(".csv"):
        try:
            return pd.read_csv(path, encoding="utf-8")
        except Exception:
            return pd.read_csv(path, encoding="gbk")
    if lower.endswith(".xlsx") or lower.endswith(".xls"):
        return pd.read_excel(path)
    raise ValueError("不支持的数据文件类型（仅支持 CSV / Excel）")


def df_preview_table(df, max_rows: int = 200, max_cols: int = 20) -> Tuple[List[str], List[List[str]]]:
    cols = list(df.columns)[:max_cols]
    small = df.loc[:, cols].head(max_rows)
    headers = [str(c) for c in small.columns]
    rows: List[List[str]] = []
    for _, row in small.iterrows():
        rows.append([("" if v is None else str(v)) for v in row.values.tolist()])
    return headers, rows


def df_to_text(df, max_rows: int = 200) -> str:
    return df.head(max_rows).to_csv(index=False)

