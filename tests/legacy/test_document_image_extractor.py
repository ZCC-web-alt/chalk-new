import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_extract_document_pdf_images_writes_per_document_files(tmp_path):
    import document_image_extractor as extractor

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    doc_dir = tmp_path / "data" / "visualizations" / "user_7" / "doc_42"
    doc_dir.mkdir(parents=True)
    stale = doc_dir / "pdf_image_99.png"
    stale.write_bytes(b"stale")

    calls = []
    original = extractor.extract_images_from_pdf
    try:
        def fake_extract(path, pages=None, filter_small=True):
            calls.append((path, pages, filter_small))
            return [
                (b"first", "png", 640, 480),
                (b"second", "jpg", 800, 600),
            ]

        extractor.extract_images_from_pdf = fake_extract
        paths = extractor.extract_document_pdf_images(
            str(pdf),
            str(tmp_path),
            user_id=7,
            doc_id=42,
            use_llm_page_hints=False,
            clear_existing=True,
        )
    finally:
        extractor.extract_images_from_pdf = original

    assert calls == [(str(pdf), None, True)]
    assert [Path(p).name for p in paths] == ["pdf_image_1.png", "pdf_image_2.jpg"]
    assert not stale.exists()
    assert (doc_dir / "pdf_image_1.png").read_bytes() == b"first"
    assert (doc_dir / "pdf_image_2.jpg").read_bytes() == b"second"


def test_discover_document_images_ignores_legacy_flat_directory(tmp_path):
    from multimodal_pipeline import discover_document_images

    flat_dir = tmp_path / "data" / "visualizations" / "user_7_pdf_images"
    flat_dir.mkdir(parents=True)
    (flat_dir / "img_1.png").write_bytes(b"wrong document")

    doc_dir = tmp_path / "data" / "visualizations" / "user_7" / "doc_42"
    doc_dir.mkdir(parents=True)
    (doc_dir / "notes.txt").write_text("not an image", encoding="utf-8")
    (doc_dir / "pdf_image_2.jpg").write_bytes(b"two")
    (doc_dir / "pdf_image_1.png").write_bytes(b"one")

    paths = discover_document_images(str(tmp_path), user_id=7, doc_id=42)

    assert [Path(p).name for p in paths] == ["pdf_image_1.png", "pdf_image_2.jpg"]
    assert all("user_7_pdf_images" not in p for p in paths)


def test_discover_document_images_extracts_with_shared_document_extractor(tmp_path):
    import document_image_extractor as extractor
    from multimodal_pipeline import discover_document_images

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    calls = []
    original = extractor.extract_images_from_pdf
    try:
        def fake_extract(path, pages=None, filter_small=True):
            calls.append((path, pages, filter_small))
            return [(b"image", "png", 640, 480)]

        extractor.extract_images_from_pdf = fake_extract
        paths = discover_document_images(
            str(tmp_path),
            user_id=7,
            doc_id=42,
            doc_source_path=str(pdf),
        )
    finally:
        extractor.extract_images_from_pdf = original

    assert calls == [(str(pdf), None, True)]
    assert [Path(p).name for p in paths] == ["pdf_image_1.png"]
    assert (tmp_path / "data" / "visualizations" / "user_7" / "doc_42" / "pdf_image_1.png").exists()


if __name__ == "__main__":
    with TemporaryDirectory() as td:
        test_extract_document_pdf_images_writes_per_document_files(Path(td))
    with TemporaryDirectory() as td:
        test_discover_document_images_ignores_legacy_flat_directory(Path(td))
    with TemporaryDirectory() as td:
        test_discover_document_images_extracts_with_shared_document_extractor(Path(td))
    print("document image extractor tests passed")
