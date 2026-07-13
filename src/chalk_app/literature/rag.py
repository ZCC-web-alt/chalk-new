"""
语义检索模块 — 基于 DashScope text-embedding-v3。

提供:
  - embed_texts(): 将文本编码为语义向量（DashScope API）
  - embed_texts_local(): 离线 fallback（HashingVectorizer）
  - search_similar_chunks(): 语义检索文献片段
  - build_hypothesis_context(): 为假设管线构建精准上下文（替代全文截断）
"""

import os
import logging
from typing import List, Tuple, Optional

import numpy as np
import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from db import DocumentChunk, Document

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# 嵌入维度常量
# ─────────────────────────────────────────────────────────────

# DashScope text-embedding-v3 输出维度（默认 1024）
EMBEDDING_DIM = 1024
# HashingVectorizer fallback 维度
_LOCAL_DIM = 512

# DashScope Embedding API
DASHSCOPE_EMBED_URL = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding"


# ─────────────────────────────────────────────────────────────
# 语义嵌入（DashScope text-embedding-v3）
# ─────────────────────────────────────────────────────────────

def _get_api_key() -> str:
    """获取 DashScope API Key"""
    key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("QWEN_API_KEY")
    if not key:
        raise ValueError(
            "未配置 API Key。请设置环境变量 DASHSCOPE_API_KEY，"
            "或在界面「API 设置」中输入。"
        )
    return key


def embed_texts(texts: List[str], api_key: Optional[str] = None) -> np.ndarray:
    """
    将若干文本编码为语义向量（float32），使用 DashScope text-embedding-v3。

    如果 API 调用失败，自动 fallback 到本地 HashingVectorizer。

    Args:
        texts: 待编码的文本列表
        api_key: 可选的 API Key（默认从环境变量读取）

    Returns:
        np.ndarray, shape=(len(texts), EMBEDDING_DIM), dtype=float32, L2 归一化
    """
    if not texts:
        return np.zeros((0, EMBEDDING_DIM), dtype="float32")

    try:
        key = api_key or _get_api_key()
        return _embed_dashscope(texts, key)
    except Exception as e:
        logger.warning(f"DashScope embedding 失败，fallback 到本地: {e}")
        return embed_texts_local(texts)


def _embed_dashscope(texts: List[str], api_key: str) -> np.ndarray:
    """调用 DashScope text-embedding-v3 API"""
    all_vecs = []
    # API 单次最多 25 条文本
    batch_size = 25
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        resp = requests.post(
            DASHSCOPE_EMBED_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "text-embedding-v3",
                "input": {"texts": batch},
                "parameters": {"dimension": EMBEDDING_DIM},
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        # 解析响应
        embeddings = data["output"]["embeddings"]
        # 按 index 排序确保顺序一致
        embeddings.sort(key=lambda x: x["text_index"])
        vecs = [np.array(e["embedding"], dtype="float32") for e in embeddings]
        all_vecs.extend(vecs)

    mat = np.stack(all_vecs, axis=0)
    # L2 归一化
    norms = np.linalg.norm(mat, axis=1, keepdims=True) + 1e-8
    return (mat / norms).astype("float32")


# ─────────────────────────────────────────────────────────────
# 本地 fallback（HashingVectorizer，无语义但离线可用）
# ─────────────────────────────────────────────────────────────

_local_vectorizer = None


def _get_local_vectorizer():
    """懒加载 HashingVectorizer（仅在 fallback 时使用）"""
    global _local_vectorizer
    if _local_vectorizer is None:
        from sklearn.feature_extraction.text import HashingVectorizer
        _local_vectorizer = HashingVectorizer(
            n_features=_LOCAL_DIM,
            alternate_sign=False,
            norm=None,
        )
    return _local_vectorizer


def embed_texts_local(texts: List[str]) -> np.ndarray:
    """
    离线 fallback：使用 HashingVectorizer 做文本向量化。
    返回 (n_samples, _LOCAL_DIM) 的 float32 数组，L2 归一化。
    """
    if not texts:
        return np.zeros((0, _LOCAL_DIM), dtype="float32")

    vectorizer = _get_local_vectorizer()
    X = vectorizer.transform(texts)
    arr = X.astype("float32").toarray()
    norms = np.linalg.norm(arr, axis=1, keepdims=True) + 1e-6
    return (arr / norms).astype("float32")


# ─────────────────────────────────────────────────────────────
# 语义检索
# ─────────────────────────────────────────────────────────────

def _detect_embedding_dim(embedding_bytes: bytes) -> int:
    """从 embedding 二进制数据推断维度"""
    return len(embedding_bytes) // 4  # float32 = 4 bytes


def search_similar_chunks(
    session: Session,
    user_id: int,
    query: str,
    top_k: int = 5,
    doc_id: int | None = None,
    api_key: Optional[str] = None,
) -> List[Tuple[DocumentChunk, float]]:
    """
    在当前用户的文献片段中做语义检索（余弦相似度）。

    自动兼容新旧两种 embedding 格式：
      - 旧格式：HashingVectorizer 512 维
      - 新格式：text-embedding-v3 1024 维

    检索时 query 的向量维度会自动匹配数据库中的维度。
    如果维度不匹配（新旧混合），自动 re-embed query。

    Args:
        session: SQLAlchemy Session
        user_id: 用户 ID
        query: 查询文本
        top_k: 返回前 K 个结果
        doc_id: 限定文档 ID（可选）
        api_key: DashScope API Key（可选）

    Returns:
        [(chunk, score)]，score 越大越相关
    """
    stmt = select(DocumentChunk).where(DocumentChunk.user_id == user_id)
    if doc_id is not None:
        stmt = stmt.where(DocumentChunk.document_id == doc_id)
    chunks: List[DocumentChunk] = list(session.execute(stmt).scalars())
    if not chunks:
        return []

    # 推断数据库中的 embedding 维度
    sample_dim = _detect_embedding_dim(chunks[0].embedding)
    query_vec = _embed_query(query, target_dim=sample_dim, api_key=api_key)

    mat = np.stack(
        [np.frombuffer(c.embedding, dtype="float32") for c in chunks], axis=0
    )
    q = query_vec / (np.linalg.norm(query_vec) + 1e-8)
    m = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-8)
    sims = (m @ q).tolist()

    idx_sorted = sorted(range(len(chunks)), key=lambda i: sims[i], reverse=True)[
        :top_k
    ]
    results = [(chunks[i], float(sims[i])) for i in idx_sorted]
    return results


def _embed_query(query: str, target_dim: int, api_key: Optional[str] = None) -> np.ndarray:
    """
    根据目标维度选择合适的嵌入方式编码 query。

    - target_dim == EMBEDDING_DIM (1024): 使用 DashScope text-embedding-v3
    - target_dim == _LOCAL_DIM (512): 使用 HashingVectorizer fallback
    - 其他维度: 尝试 DashScope，失败则 fallback
    """
    if target_dim == _LOCAL_DIM:
        return embed_texts_local([query])[0]

    # 默认尝试 DashScope
    try:
        key = api_key or _get_api_key()
        return _embed_dashscope([query], key)[0]
    except Exception:
        # fallback 到本地
        if target_dim == _LOCAL_DIM:
            return embed_texts_local([query])[0]
        # 维度不匹配且 API 不可用，返回零向量
        logger.warning(f"无法生成 {target_dim} 维向量，API 不可用且本地维度不匹配")
        return np.zeros(target_dim, dtype="float32")


# ─────────────────────────────────────────────────────────────
# 假设管线专用：智能上下文构建
# ─────────────────────────────────────────────────────────────

def search_with_context(
    session: Session,
    user_id: int,
    query: str,
    top_k: int = 5,
    context_window: int = 1,
    doc_id: int | None = None,
    api_key: Optional[str] = None,
) -> List[dict]:
    """
    语义检索 + 上下文窗口扩展。

    对每个命中 chunk，自动返回前后各 context_window 个 chunk，
    并标注完整性信息，防止断章取义。

    Returns:
        [{"text", "score", "source_title", "document_id", "chunk_id",
          "is_truncated", "has_prev", "has_next", "context_markers"}]
    """
    results = search_similar_chunks(
        session, user_id, query,
        top_k=top_k, doc_id=doc_id, api_key=api_key,
    )
    if not results:
        return []

    expanded = []
    for chunk, score in results:
        prev_chunks = list(session.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == chunk.document_id)
            .where(DocumentChunk.order < chunk.order)
            .order_by(DocumentChunk.order.desc())
            .limit(context_window)
        ).scalars())

        next_chunks = list(session.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == chunk.document_id)
            .where(DocumentChunk.order > chunk.order)
            .order_by(DocumentChunk.order.asc())
            .limit(context_window)
        ).scalars())

        parts = []
        for pc in reversed(prev_chunks):
            parts.append(f"[前文续接] {pc.text}")
        parts.append(chunk.text)
        for nc in next_chunks:
            parts.append(f"[后文延续] {nc.text}")

        markers = []
        if prev_chunks:
            markers.append("有前文续接")
        if next_chunks:
            markers.append("有后文延续")
        is_trunc = getattr(chunk, 'is_truncated', False)
        if is_trunc:
            markers.append("此片段可能被截断")

        doc = session.get(Document, chunk.document_id)
        expanded.append({
            "text": "\n".join(parts),
            "score": score,
            "source_title": doc.title if doc else "未知文档",
            "document_id": chunk.document_id,
            "chunk_id": chunk.id,
            "is_truncated": is_trunc,
            "has_prev": bool(prev_chunks),
            "has_next": bool(next_chunks),
            "context_markers": "[" + "][".join(markers) + "]" if markers else "",
        })

    return expanded


def build_hypothesis_context(
    session: Session,
    user_id: int,
    research_question: str,
    doc_id: Optional[int] = None,
    max_chunks: int = 20,
    min_score: float = 0.3,
    api_key: Optional[str] = None,
) -> str:
    """
    为假设管线构建精准的文献上下文（替代全文截断）。

    工作流程：
      1. 从 research_question 生成查询向量
      2. 语义检索所有相关文献片段（top_k=max_chunks）
      3. 按相关度排序，过滤低分片段
      4. 拼接为结构化上下文文本

    如果语义检索不可用（API 不可用、无 embedding 等），
    自动 fallback 到按文档顺序取前 N 个片段。

    Args:
        session: SQLAlchemy Session
        user_id: 用户 ID
        research_question: 研究问题（用作检索 query）
        doc_id: 限定文档 ID（None 表示所有文档）
        max_chunks: 最多返回的片段数
        min_score: 最低相关度阈值（0-1）
        api_key: DashScope API Key

    Returns:
        拼接好的上下文文本
    """
    try:
        results = search_with_context(
            session, user_id, research_question,
            top_k=max_chunks, context_window=1,
            doc_id=doc_id, api_key=api_key,
        )
        filtered = [r for r in results if r["score"] >= min_score]

        if filtered:
            parts = []
            for i, r in enumerate(filtered, 1):
                markers = r.get("context_markers", "")
                marker_tag = f" {markers}" if markers else ""
                source_tag = f"文档ID={r['document_id']}"
                parts.append(
                    f"[相关片段 {i} | {source_tag} | 相关度={r['score']:.3f}{marker_tag}]\n{r['text']}"
                )
            context = "\n\n".join(parts)
            logger.info(
                f"build_hypothesis_context: 语义检索+上下文扩展返回 {len(filtered)} 个片段"
            )
            return context

    except Exception as e:
        logger.warning(f"语义检索失败，fallback 到顺序取片段: {e}")

    # 2. Fallback：按文档顺序取前 N 个片段
    return _fallback_context(session, user_id, doc_id, max_chunks)


def _fallback_context(
    session: Session,
    user_id: int,
    doc_id: Optional[int],
    max_chunks: int,
) -> str:
    """顺序取片段的 fallback"""
    stmt = select(DocumentChunk).where(DocumentChunk.user_id == user_id)
    if doc_id is not None:
        stmt = stmt.where(DocumentChunk.document_id == doc_id)
    stmt = stmt.order_by(DocumentChunk.order).limit(max_chunks)

    chunks = list(session.execute(stmt).scalars())
    if not chunks:
        return ""

    parts = []
    for i, chunk in enumerate(chunks, 1):
        parts.append(f"[文献片段 {i} | 文档ID={chunk.document_id}]\n{chunk.text}")
    return "\n\n".join(parts)


# ─────────────────────────────────────────────────────────────
# 嵌入升级：将旧 HashingVectorizer 向量替换为 text-embedding-v3
# ─────────────────────────────────────────────────────────────

def reembed_all_chunks(
    session: Session,
    user_id: Optional[int] = None,
    api_key: Optional[str] = None,
    batch_size: int = 25,
    on_progress: Optional[callable] = None,
) -> dict:
    """
    将数据库中所有旧的 512 维 HashingVectorizer embedding 替换为
    1024 维 text-embedding-v3 语义向量。

    Args:
        session: SQLAlchemy Session
        user_id: 限定用户 ID（None 表示所有用户）
        api_key: DashScope API Key
        batch_size: 每次 API 调用的文本数（最多 25）
        on_progress: 进度回调 (current, total)

    Returns:
        {"updated": 更新数量, "failed": 失败数量, "skipped": 跳过数量}
    """
    stmt = select(DocumentChunk)
    if user_id is not None:
        stmt = stmt.where(DocumentChunk.user_id == user_id)
    stmt = stmt.order_by(DocumentChunk.id)

    chunks = list(session.execute(stmt).scalars())
    if not chunks:
        return {"updated": 0, "failed": 0, "skipped": 0}

    # 找出需要更新的 chunk（维度 != EMBEDDING_DIM）
    to_update = []
    for c in chunks:
        dim = _detect_embedding_dim(c.embedding)
        if dim != EMBEDDING_DIM:
            to_update.append(c)

    if not to_update:
        return {"updated": 0, "failed": 0, "skipped": len(chunks)}

    key = api_key or _get_api_key()
    updated = 0
    failed = 0

    for i in range(0, len(to_update), batch_size):
        batch = to_update[i : i + batch_size]
        texts = [c.text[:8192] for c in batch]  # text-embedding-v3 最大 8192 tokens

        try:
            vecs = _embed_dashscope(texts, key)
            for chunk, vec in zip(batch, vecs):
                chunk.embedding = vec.tobytes()
            session.commit()
            updated += len(batch)
        except Exception as e:
            logger.error(f"Re-embed batch {i} failed: {e}")
            session.rollback()
            failed += len(batch)

        if on_progress:
            on_progress(i + len(batch), len(to_update))

    return {"updated": updated, "failed": failed, "skipped": len(chunks) - len(to_update)}
