# -*- coding: utf-8 -*-
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from multi_document_context import DocumentContextItem, build_multi_document_context


def test_build_multi_document_context_labels_each_source():
    context, metadata = build_multi_document_context(
        [
            DocumentContextItem(
                document_id=1,
                title="Battery interface paper",
                source_type="pdf",
                text="High-Ni cathode surface oxygen release increases Rct.",
            ),
            DocumentContextItem(
                document_id=2,
                title="Electrolyte additive paper",
                source_type="doi",
                text="Fluorinated additives improve CEI stability and capacity retention.",
            ),
        ]
    )

    assert "【多文献输入上下文】" in context
    assert "共输入 2 篇用户文献" in context
    assert "[D1 | document_id=1 | source_type=pdf]" in context
    assert "[D2 | document_id=2 | source_type=doi]" in context
    assert "Battery interface paper" in context
    assert "Electrolyte additive paper" in context
    assert metadata[0]["source_id"] == "D1"
    assert metadata[1]["source_id"] == "D2"
    assert metadata[0]["document_id"] == 1
    assert metadata[1]["document_id"] == 2


def test_build_multi_document_context_tracks_truncation():
    context, metadata = build_multi_document_context(
        [
            DocumentContextItem(document_id=10, title="Long paper", text="A" * 1600),
            DocumentContextItem(document_id=11, title="Second paper", text="B" * 1600),
        ],
        per_document_char_limit=1200,
        total_char_limit=2600,
    )

    assert "[该文献内容已按输入长度限制截断" in context
    assert metadata[0]["truncated"] is True
    assert metadata[0]["included_chars"] == 1200
    assert metadata[1]["source_id"] == "D2"


if __name__ == "__main__":
    test_build_multi_document_context_labels_each_source()
    test_build_multi_document_context_tracks_truncation()
    print("multi-document context tests passed")
