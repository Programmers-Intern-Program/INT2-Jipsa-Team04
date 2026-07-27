"""Qdrant 검색 범위와 OCR source_metadata 복원 계약을 검증한다."""

from qdrant_client import models

from jipsa_rag.infrastructure.indexing.qdrant_search import (
    _to_chunk_search_hit,
    build_user_active_reference_chunk_filter,
)


def test_filter_keeps_user_active_and_selected_file_conditions() -> None:
    query_filter = build_user_active_reference_chunk_filter(
        user_idx=45,
        reference_file_idxs=(101, 202),
    )

    assert query_filter.must is not None
    keys = tuple(
        condition.key
        for condition in query_filter.must
        if isinstance(condition, models.FieldCondition)
    )
    assert keys == ("users_idx", "is_active", "file_idx")


def test_qdrant_hit_preserves_nested_ocr_metadata() -> None:
    chunk_id = "11111111-1111-1111-1111-111111111111"
    point = models.ScoredPoint(
        id=chunk_id,
        version=1,
        score=0.91,
        payload={
            "chunk_id": chunk_id,
            "users_idx": 45,
            "rag_document_idx": 1001,
            "file_idx": 202,
            "folder_idx": 9,
            "file_name": "성과.xlsx",
            "file_type": "xlsx",
            "chunk_index": 0,
            "content": "[이미지 OCR]\n증가 추세",
            "token_count": 20,
            "page": None,
            "slide_no": None,
            "sheet_name": "성과",
            "section_title": None,
            "parser_version": "1.0.0",
            "embedding_model": "Qwen/Qwen3-Embedding-0.6B",
            "index_version": 2,
            "is_active": True,
            "source_metadata": {
                "sheet_name": "성과",
                "cell_range": "B2:E10",
                "content_origin": "ocr",
                "unit_type": "ocr_image",
                "image_index": 2,
                "image_id": "chart-2",
                "image_kind": "xlsx_chart_render",
            },
        },
        vector=None,
    )

    hit = _to_chunk_search_hit(
        point=point,
        expected_user_idx=45,
        expected_reference_file_idxs=frozenset({202}),
        expected_embedding_model="Qwen/Qwen3-Embedding-0.6B",
    )

    assert hit.source_metadata["content_origin"] == "ocr"
    assert hit.source_metadata["image_id"] == "chart-2"
