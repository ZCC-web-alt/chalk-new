from __future__ import annotations


def test_multimodal_package_exports_legacy_functions() -> None:
    from multimodal import (
        analyze_excel_data,
        analyze_scientific_image,
        multimodal_to_context,
        parse_table_from_image,
    )

    assert callable(analyze_excel_data)
    assert callable(analyze_scientific_image)
    assert callable(multimodal_to_context)
    assert callable(parse_table_from_image)
