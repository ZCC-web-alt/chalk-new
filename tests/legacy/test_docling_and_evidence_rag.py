from docling_parser import (
    ParsedDocument,
    chunks_from_parsed_document,
    markdown_to_blocks,
)
from scientific_evidence_rag import EvidenceAnswer, format_evidence_context


def test_docling_markdown_blocks_preserve_scientific_structure():
    markdown = """# Results

Fe-N-C activity improved after asymmetric coordination.

| sample | E1/2 |
| --- | --- |
| Fe-N3C1 | 0.84 |

Fig. 2 Catalytic volcano relationship.

DeltaG = 0.32 eV
"""

    blocks = markdown_to_blocks(markdown)
    chunk_types = [block.chunk_type for block in blocks]

    assert "heading" in chunk_types
    assert "table_data" in chunk_types
    assert "figure_caption" in chunk_types
    assert "formula" in chunk_types


def test_docling_chunks_carry_type_and_parser_label():
    parsed = ParsedDocument(
        text="",
        parser="docling",
        blocks=markdown_to_blocks(
            "| sample | E1/2 |\n| --- | --- |\n| Fe-N3C1 | 0.84 |\n"
        ),
    )

    chunks = chunks_from_parsed_document(parsed)

    assert chunks
    assert chunks[0][2] == "table_data"
    assert "[\u8868\u683c | parser=docling]" in chunks[0][1]


def test_evidence_context_format_is_hypothesis_ready():
    evidence = EvidenceAnswer(
        question="How can ORR activity be improved?",
        answer="Fe-N3C1 sites may lower the reaction barrier.",
        context="[evidence 1] Fe-N3C1 shows high half-wave potential.",
        citations=[{"title": "Fe-N-C Active Sites", "locator": "chunk 1"}],
        source="paperqa",
        status="ok",
    )

    context = format_evidence_context(evidence)

    assert "PaperQA/\u79d1\u5b66\u8bc1\u636e RAG \u4e0a\u4e0b\u6587" in context
    assert "Fe-N-C Active Sites" in context
    assert "Fe-N3C1" in context
