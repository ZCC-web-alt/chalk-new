import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from sqlalchemy import (
    create_engine,
    String,
    Integer,
    DateTime,
    ForeignKey,
    LargeBinary,
    Text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    sessionmaker,
    Session,
)


import sys
from chalk_app.paths import PROJECT_ROOT

# determine base directory for data/config storage
if getattr(sys, "frozen", False):
    # running in a bundle (PyInstaller exe)
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = str(PROJECT_ROOT)

# store data directory next to executable (or source file during development)
configured_data_dir = os.environ.get("CHALK_WEB_DATA_DIR", "").strip()
if configured_data_dir:
    data_path = Path(configured_data_dir).expanduser()
    if not data_path.is_absolute():
        data_path = PROJECT_ROOT / data_path
    DATA_DIR = str(data_path.resolve())
else:
    DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "app.db")
ENGINE = create_engine(f"sqlite:///{DB_PATH}", echo=False, future=True)
SessionLocal = sessionmaker(bind=ENGINE, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    documents: Mapped[List["Document"]] = relationship(back_populates="user")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)  # pdf/web/other
    source_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="documents")
    chunks: Mapped[List["DocumentChunk"]] = relationship(back_populates="document")


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    chunk_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="text"
    )  # "text" / "image_analysis" / "table_data"

    document: Mapped[Document] = relationship(back_populates="chunks")


class UserGlossary(Base):
    """用户私人化工术语库：英文原词 <-> 中文译名"""
    __tablename__ = "user_glossary"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    en_term: Mapped[str] = mapped_column(String(200), nullable=False)
    zh_term: Mapped[str] = mapped_column(String(200), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )


class LabRecord(Base):
    """实验记录本：每条记录对应一次实验"""
    __tablename__ = "lab_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    related_doc_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON list of doc IDs
    ai_suggestion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class Hypothesis(Base):
    """科学假设：存储生成的假设与研究计划"""
    __tablename__ = "hypotheses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    research_question: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # 完整 JSON 结果
    confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    feasibility: Mapped[str] = mapped_column(String(20), nullable=False, default="中")
    iteration_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    related_doc_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extra_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 迭代/评审/推理链/验证等补充数据
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")  # draft/reviewed/approved/rejected
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class HypothesisFeedback(Base):
    """人在回路反馈记录：每次假设生成的完整交互历史"""
    __tablename__ = "hypothesis_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hypothesis_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("hypotheses.id"), nullable=True  # 假设保存后再关联
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    session_id: Mapped[str] = mapped_column(
        String(36), nullable=False, index=True  # UUID, 每次生成唯一
    )
    interaction_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}"  # 完整 HITL 交互历史 JSON
    )
    mode: Mapped[str] = mapped_column(
        String(10), nullable=False, default="auto"  # "hitl" 或 "auto"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )


class MultimodalAnalysis(Base):
    """多模态分析结果缓存（避免重复调用 API）"""
    __tablename__ = "multimodal_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    image_path: Mapped[str] = mapped_column(String(500), nullable=False)
    image_type: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    # 原始分析结果
    raw_analysis: Mapped[str] = mapped_column(Text, nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 清洗后的结构化数据 (JSON)
    cleaned_data_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # 关联挖掘结果 (JSON)
    associations_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # 元信息
    model_used: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    # "pending" / "analyzed" / "cleaned" / "mined" / "error"
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )


class LiteratureEvidence(Base):
    """从用户导入文献、摘要、DOI 或图表描述中抽取的结构化证据。"""
    __tablename__ = "literature_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    document_id: Mapped[Optional[int]] = mapped_column(ForeignKey("documents.id"), nullable=True, index=True)
    hypothesis_id: Mapped[Optional[int]] = mapped_column(ForeignKey("hypotheses.id"), nullable=True, index=True)
    domain: Mapped[str] = mapped_column(String(50), nullable=False, default="", index=True)
    paper_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    doi: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    year: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    journal: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    material_system: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    reaction_type: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    battery_type: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    ion_type: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    key_data: Mapped[str] = mapped_column(Text, nullable=False, default="")
    key_mechanism: Mapped[str] = mapped_column(Text, nullable=False, default="")
    limitation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_kind: Mapped[str] = mapped_column(String(50), nullable=False, default="user_imported")
    reliability_level: Mapped[str] = mapped_column(String(50), nullable=False, default="needs_verification", index=True)
    extra_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class DomainEvidence(Base):
    """电池/电催化领域性能指标、机理和实验记录证据。"""
    __tablename__ = "domain_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    literature_evidence_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("literature_evidence.id"), nullable=True, index=True
    )
    lab_record_id: Mapped[Optional[int]] = mapped_column(ForeignKey("lab_records.id"), nullable=True, index=True)
    hypothesis_id: Mapped[Optional[int]] = mapped_column(ForeignKey("hypotheses.id"), nullable=True, index=True)
    domain: Mapped[str] = mapped_column(String(50), nullable=False, default="", index=True)
    material_system: Mapped[str] = mapped_column(String(255), nullable=False, default="", index=True)
    reaction_type: Mapped[str] = mapped_column(String(80), nullable=False, default="", index=True)
    battery_type: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    ion_type: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    metric_name: Mapped[str] = mapped_column(String(120), nullable=False, default="", index=True)
    metric_value: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    metric_unit: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    condition_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    baseline: Mapped[str] = mapped_column(Text, nullable=False, default="")
    key_mechanism: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_kind: Mapped[str] = mapped_column(String(50), nullable=False, default="manual")
    reliability_level: Mapped[str] = mapped_column(String(50), nullable=False, default="needs_verification", index=True)
    extra_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class ComputationalCatalysisEvidence(Base):
    """OCP/FAIR-Chem 等计算催化数据源的轻量证据索引。"""
    __tablename__ = "computational_catalysis_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(50), nullable=False, default="electrocatalysis", index=True)
    dataset_name: Mapped[str] = mapped_column(String(120), nullable=False, default="", index=True)
    task_type: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    reaction_context: Mapped[str] = mapped_column(String(80), nullable=False, default="", index=True)
    material_system: Mapped[str] = mapped_column(String(255), nullable=False, default="", index=True)
    surface_facet: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    adsorbate: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    electrolyte_or_solvent: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    dft_energy: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    adsorption_energy: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    model_name: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    predicted_value: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    reference_value: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    source_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    evidence_kind: Mapped[str] = mapped_column(String(50), nullable=False, default="computational")
    reliability_level: Mapped[str] = mapped_column(String(50), nullable=False, default="catalog", index=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    extra_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


def init_db() -> None:
    Base.metadata.create_all(ENGINE)
    # --- 增量迁移：为旧数据库补齐新增列/表 ---
    _run_migrations()


def _run_migrations() -> None:
    """对已存在的数据库执行 ALTER TABLE / CREATE TABLE 迁移。"""
    import sqlite3 as _sqlite
    raw = _sqlite.connect(str(DB_PATH))
    try:
        cur = raw.cursor()

        # 1) document_chunks.chunk_type
        cur.execute("PRAGMA table_info(document_chunks)")
        cols = {r[1] for r in cur.fetchall()}
        if "chunk_type" not in cols:
            cur.execute(
                "ALTER TABLE document_chunks ADD COLUMN chunk_type TEXT DEFAULT 'text'"
            )
            raw.commit()

        # 2) multimodal_analyses 表
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='multimodal_analyses'"
        )
        if not cur.fetchone():
            cur.execute(
                """CREATE TABLE IF NOT EXISTS multimodal_analyses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER,
                    user_id INTEGER,
                    image_path TEXT,
                    image_type TEXT,
                    raw_analysis TEXT,
                    summary TEXT,
                    cleaned_data_json TEXT,
                    associations_json TEXT,
                    model_used TEXT,
                    status TEXT DEFAULT 'pending',
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            raw.commit()

        # 3) hypotheses.extra_json
        cur.execute("PRAGMA table_info(hypotheses)")
        cols = {r[1] for r in cur.fetchall()}
        if "extra_json" not in cols:
            cur.execute("ALTER TABLE hypotheses ADD COLUMN extra_json TEXT")
            raw.commit()

        # 4) 结构化证据库表：只新增表，不修改/删除任何用户、文献或实验记录数据
        cur.execute(
            """CREATE TABLE IF NOT EXISTS literature_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                document_id INTEGER,
                hypothesis_id INTEGER,
                domain TEXT DEFAULT '',
                paper_id TEXT,
                title TEXT DEFAULT '',
                doi TEXT DEFAULT '',
                year TEXT,
                journal TEXT,
                material_system TEXT DEFAULT '',
                reaction_type TEXT DEFAULT '',
                battery_type TEXT DEFAULT '',
                ion_type TEXT DEFAULT '',
                key_data TEXT DEFAULT '',
                key_mechanism TEXT DEFAULT '',
                limitation TEXT DEFAULT '',
                source_text TEXT DEFAULT '',
                source_kind TEXT DEFAULT 'user_imported',
                reliability_level TEXT DEFAULT 'needs_verification',
                extra_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        cur.execute(
            """CREATE TABLE IF NOT EXISTS domain_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                literature_evidence_id INTEGER,
                lab_record_id INTEGER,
                hypothesis_id INTEGER,
                domain TEXT DEFAULT '',
                material_system TEXT DEFAULT '',
                reaction_type TEXT DEFAULT '',
                battery_type TEXT DEFAULT '',
                ion_type TEXT DEFAULT '',
                metric_name TEXT DEFAULT '',
                metric_value TEXT DEFAULT '',
                metric_unit TEXT DEFAULT '',
                condition_text TEXT DEFAULT '',
                baseline TEXT DEFAULT '',
                key_mechanism TEXT DEFAULT '',
                source_text TEXT DEFAULT '',
                source_kind TEXT DEFAULT 'manual',
                reliability_level TEXT DEFAULT 'needs_verification',
                extra_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        cur.execute(
            """CREATE TABLE IF NOT EXISTS computational_catalysis_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                domain TEXT DEFAULT 'electrocatalysis',
                dataset_name TEXT DEFAULT '',
                task_type TEXT DEFAULT '',
                reaction_context TEXT DEFAULT '',
                material_system TEXT DEFAULT '',
                surface_facet TEXT DEFAULT '',
                adsorbate TEXT DEFAULT '',
                electrolyte_or_solvent TEXT DEFAULT '',
                dft_energy TEXT DEFAULT '',
                adsorption_energy TEXT DEFAULT '',
                model_name TEXT DEFAULT '',
                predicted_value TEXT DEFAULT '',
                reference_value TEXT DEFAULT '',
                source_url TEXT DEFAULT '',
                evidence_kind TEXT DEFAULT 'computational',
                reliability_level TEXT DEFAULT 'catalog',
                notes TEXT DEFAULT '',
                extra_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        for table, columns in {
            "literature_evidence": ["user_id", "document_id", "domain", "reliability_level"],
            "domain_evidence": ["user_id", "domain", "material_system", "reaction_type", "metric_name", "reliability_level"],
            "computational_catalysis_evidence": ["user_id", "domain", "dataset_name", "reaction_context", "material_system", "reliability_level"],
        }.items():
            for col in columns:
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{table}_{col} ON {table} ({col})"
                )
        raw.commit()
    finally:
        raw.close()


def get_session() -> Session:
    return SessionLocal()


def create_document(
    session: Session,
    user_id: int,
    title: str,
    source_type: str,
    source_path: Optional[str],
    summary: Optional[str] = None,
) -> Document:
    doc = Document(
        user_id=user_id,
        title=title,
        source_type=source_type,
        source_path=source_path,
        summary=summary,
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def add_document_chunks(
    session: Session,
    user_id: int,
    document_id: int,
    chunks: List[Tuple],
) -> None:
    for item in chunks:
        order, text, emb = item[:3]
        chunk_type = item[3] if len(item) >= 4 and item[3] else "text"
        session.add(
            DocumentChunk(
                document_id=document_id,
                user_id=user_id,
                order=order,
                text=text,
                embedding=emb,
                chunk_type=str(chunk_type)[:20],
            )
        )
    session.commit()


def reimport_document_chunks(
    session: Session,
    document_id: int,
    new_chunks: List[Tuple],
) -> int:
    """删除旧 chunks 并用新数据替换，返回替换的 chunk 数量。

    用于修复 pypdf 乱码：用 fitz 重新提取文本后替换 DB 中的旧 chunks。
    """
    # 1. 删除旧 chunks
    deleted = session.query(DocumentChunk).filter(
        DocumentChunk.document_id == document_id
    ).delete()
    session.flush()

    # 2. 插入新 chunks
    for item in new_chunks:
        order, text, emb = item[:3]
        chunk_type = item[3] if len(item) >= 4 and item[3] else "text"
        chunk = DocumentChunk(
            document_id=document_id,
            user_id=session.query(Document).get(document_id).user_id,
            order=order,
            text=text,
            embedding=emb,
            chunk_type=str(chunk_type)[:20],
        )
        session.add(chunk)
    session.commit()
    return len(new_chunks)


def find_garbled_documents(session: Session, user_id: int) -> List[Document]:
    """查找 chunks 中含有乱码的文档列表。

    通过采样第一个 chunk 的文本，检测 Indic/Thai 等乱码字符区间。
    """
    from pdf_utils import is_garbled_text

    docs = session.query(Document).filter(Document.user_id == user_id).all()
    garbled = []
    for doc in docs:
        first_chunk = session.query(DocumentChunk).filter(
            DocumentChunk.document_id == doc.id
        ).order_by(DocumentChunk.order.asc()).first()
        if first_chunk and is_garbled_text(first_chunk.text):
            garbled.append(doc)
    return garbled


# ---- 查询与导出 ----

def search_documents(
    session: Session,
    user_id: int,
    keyword: Optional[str] = None,
) -> List[Document]:
    """按标题或摘要关键字搜索文档"""
    query = session.query(Document).filter(Document.user_id == user_id)
    if keyword:
        kw = f"%{keyword}%"
        query = query.filter(
            (Document.title.ilike(kw)) | (Document.summary.ilike(kw))
        )
    return query.order_by(Document.created_at.desc()).all()


def _documents_to_dataframe(docs: List[Document]):
    """把 SQLAlchemy 文档列表转换为 pandas DataFrame"""
    import pandas as pd

    rows = []
    for d in docs:
        rows.append({
            "id": d.id,
            "title": d.title,
            "source_type": d.source_type,
            "source_path": d.source_path,
            "summary": d.summary,
            "created_at": d.created_at,
        })
    return pd.DataFrame(rows)


def export_documents(
    session: Session,
    user_id: int,
    path: str,
    fmt: str = "csv",
    keyword: Optional[str] = None,
) -> int:
    """按格式导出当前用户文档列表，返回行数
    fmt 可以是 csv, excel, parquet
    """
    docs = search_documents(session, user_id, keyword)
    df = _documents_to_dataframe(docs)
    lower = path.lower()
    if fmt == "csv" or lower.endswith(".csv"):
        df.to_csv(path, index=False, encoding="utf-8-sig")
    elif fmt in ("xlsx", "excel") or lower.endswith(".xlsx") or lower.endswith(".xls"):
        df.to_excel(path, index=False, engine="openpyxl")
    elif fmt == "parquet" or lower.endswith(".parquet"):
        df.to_parquet(path, index=False)
    else:
        raise ValueError("不支持的导出格式")
    return len(df)


# ─────────────────────────────────────────────────────────────
# UserGlossary CRUD
# ─────────────────────────────────────────────────────────────

def get_user_glossary(session: Session, user_id: int) -> List["UserGlossary"]:
    return (
        session.query(UserGlossary)
        .filter(UserGlossary.user_id == user_id)
        .order_by(UserGlossary.en_term.asc())
        .all()
    )


def upsert_glossary_term(
    session: Session,
    user_id: int,
    en_term: str,
    zh_term: str,
    note: Optional[str] = None,
) -> "UserGlossary":
    existing = (
        session.query(UserGlossary)
        .filter(UserGlossary.user_id == user_id, UserGlossary.en_term == en_term)
        .first()
    )
    if existing:
        existing.zh_term = zh_term
        if note is not None:
            existing.note = note
        session.commit()
        return existing
    term = UserGlossary(user_id=user_id, en_term=en_term, zh_term=zh_term, note=note)
    session.add(term)
    session.commit()
    session.refresh(term)
    return term


def delete_glossary_term(session: Session, user_id: int, term_id: int) -> bool:
    term = session.query(UserGlossary).filter(
        UserGlossary.id == term_id, UserGlossary.user_id == user_id
    ).first()
    if not term:
        return False
    session.delete(term)
    session.commit()
    return True


# ─────────────────────────────────────────────────────────────
# LabRecord CRUD
# ─────────────────────────────────────────────────────────────

def get_lab_records(session: Session, user_id: int) -> List["LabRecord"]:
    return (
        session.query(LabRecord)
        .filter(LabRecord.user_id == user_id)
        .order_by(LabRecord.updated_at.desc())
        .all()
    )


def create_lab_record(
    session: Session,
    user_id: int,
    title: str,
    content: str = "",
    related_doc_ids: Optional[str] = None,
) -> "LabRecord":
    record = LabRecord(
        user_id=user_id,
        title=title,
        content=content,
        related_doc_ids=related_doc_ids,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def update_lab_record(
    session: Session,
    record_id: int,
    user_id: int,
    title: Optional[str] = None,
    content: Optional[str] = None,
    related_doc_ids: Optional[str] = None,
    ai_suggestion: Optional[str] = None,
) -> Optional["LabRecord"]:
    record = session.query(LabRecord).filter(
        LabRecord.id == record_id, LabRecord.user_id == user_id
    ).first()
    if not record:
        return None
    if title is not None:
        record.title = title
    if content is not None:
        record.content = content
    if related_doc_ids is not None:
        record.related_doc_ids = related_doc_ids
    if ai_suggestion is not None:
        record.ai_suggestion = ai_suggestion
    record.updated_at = datetime.utcnow()
    session.commit()
    session.refresh(record)
    return record


def delete_lab_record(session: Session, user_id: int, record_id: int) -> bool:
    record = session.query(LabRecord).filter(
        LabRecord.id == record_id, LabRecord.user_id == user_id
    ).first()
    if not record:
        return False
    session.delete(record)
    session.commit()
    return True


# ─────────────────────────────────────────────────────────────
# Hypothesis CRUD
# ─────────────────────────────────────────────────────────────

def get_hypotheses(session: Session, user_id: int) -> List["Hypothesis"]:
    return (
        session.query(Hypothesis)
        .filter(Hypothesis.user_id == user_id)
        .order_by(Hypothesis.updated_at.desc())
        .all()
    )


def create_hypothesis(
    session: Session,
    user_id: int,
    title: str,
    result_json: str,
    confidence: int = 5,
    feasibility: str = "中",
    iteration_count: int = 0,
    research_question: Optional[str] = None,
    related_doc_ids: Optional[str] = None,
    extra_json: Optional[str] = None,
) -> "Hypothesis":
    hypo = Hypothesis(
        user_id=user_id,
        title=title,
        research_question=research_question,
        result_json=result_json,
        confidence=confidence,
        feasibility=feasibility,
        iteration_count=iteration_count,
        related_doc_ids=related_doc_ids,
        extra_json=extra_json,
    )
    session.add(hypo)
    session.commit()
    session.refresh(hypo)
    return hypo


def update_hypothesis(
    session: Session,
    hypothesis_id: int,
    user_id: int,
    title: Optional[str] = None,
    result_json: Optional[str] = None,
    confidence: Optional[int] = None,
    feasibility: Optional[str] = None,
    status: Optional[str] = None,
    extra_json: Optional[str] = None,
) -> Optional["Hypothesis"]:
    hypo = session.query(Hypothesis).filter(
        Hypothesis.id == hypothesis_id, Hypothesis.user_id == user_id
    ).first()
    if not hypo:
        return None
    if title is not None:
        hypo.title = title
    if result_json is not None:
        hypo.result_json = result_json
    if confidence is not None:
        hypo.confidence = confidence
    if feasibility is not None:
        hypo.feasibility = feasibility
    if status is not None:
        hypo.status = status
    if extra_json is not None:
        hypo.extra_json = extra_json
    hypo.updated_at = datetime.utcnow()
    session.commit()
    session.refresh(hypo)
    return hypo


def delete_hypothesis(session: Session, user_id: int, hypothesis_id: int) -> bool:
    hypo = session.query(Hypothesis).filter(
        Hypothesis.id == hypothesis_id, Hypothesis.user_id == user_id
    ).first()
    if not hypo:
        return False
    # 级联删除关联的反馈记录
    session.query(HypothesisFeedback).filter(
        HypothesisFeedback.hypothesis_id == hypothesis_id
    ).delete()
    session.delete(hypo)
    session.commit()
    return True


def create_hypothesis_feedback(
    session: Session,
    user_id: int,
    session_id: str,
    interaction_json: str,
    mode: str = "auto",
    hypothesis_id: Optional[int] = None,
) -> "HypothesisFeedback":
    """创建人在回路反馈记录"""
    fb = HypothesisFeedback(
        user_id=user_id,
        session_id=session_id,
        interaction_json=interaction_json,
        mode=mode,
        hypothesis_id=hypothesis_id,
    )
    session.add(fb)
    session.commit()
    session.refresh(fb)
    return fb


def update_hypothesis_feedback(
    session: Session,
    feedback_id: int,
    interaction_json: Optional[str] = None,
    hypothesis_id: Optional[int] = None,
) -> Optional["HypothesisFeedback"]:
    """更新反馈记录（关联 hypothesis_id 或更新 interaction_json）"""
    fb = session.query(HypothesisFeedback).filter(
        HypothesisFeedback.id == feedback_id
    ).first()
    if not fb:
        return None
    if interaction_json is not None:
        fb.interaction_json = interaction_json
    if hypothesis_id is not None:
        fb.hypothesis_id = hypothesis_id
    session.commit()
    session.refresh(fb)
    return fb


def get_hypothesis_feedback_by_session(
    session: Session, session_id: str
) -> Optional["HypothesisFeedback"]:
    """根据 session_id 查询反馈记录"""
    return (
        session.query(HypothesisFeedback)
        .filter(HypothesisFeedback.session_id == session_id)
        .first()
    )


# ── MultimodalAnalysis CRUD ──

def get_multimodal_analysis(
    session: Session, document_id: int, image_path: str
) -> Optional[MultimodalAnalysis]:
    """查询已有的多模态分析缓存"""
    return (
        session.query(MultimodalAnalysis)
        .filter(
            MultimodalAnalysis.document_id == document_id,
            MultimodalAnalysis.image_path == image_path,
        )
        .first()
    )


def get_multimodal_analyses_by_doc(
    session: Session, document_id: int
) -> List[MultimodalAnalysis]:
    """获取某文档的所有多模态分析结果"""
    return (
        session.query(MultimodalAnalysis)
        .filter(MultimodalAnalysis.document_id == document_id)
        .order_by(MultimodalAnalysis.id)
        .all()
    )


def upsert_multimodal_analysis(
    session: Session,
    document_id: int,
    user_id: int,
    image_path: str,
    *,
    image_type: str = "",
    raw_analysis: str = "",
    summary: str = "",
    cleaned_data_json: str = "[]",
    associations_json: str = "[]",
    model_used: str = "",
    status: str = "analyzed",
    error_message: str = "",
) -> MultimodalAnalysis:
    """创建或更新多模态分析缓存"""
    existing = get_multimodal_analysis(session, document_id, image_path)
    if existing:
        existing.image_type = image_type
        existing.raw_analysis = raw_analysis
        existing.summary = summary
        existing.cleaned_data_json = cleaned_data_json
        existing.associations_json = associations_json
        existing.model_used = model_used
        existing.status = status
        existing.error_message = error_message
        session.commit()
        session.refresh(existing)
        return existing
    ma = MultimodalAnalysis(
        document_id=document_id,
        user_id=user_id,
        image_path=image_path,
        image_type=image_type,
        raw_analysis=raw_analysis,
        summary=summary,
        cleaned_data_json=cleaned_data_json,
        associations_json=associations_json,
        model_used=model_used,
        status=status,
        error_message=error_message,
    )
    session.add(ma)
    session.commit()
    session.refresh(ma)
    return ma

