"""대상 JSON 로그에서 단계 시간과 Request ID를 추출하는 계약을 검증한다."""

from pathlib import Path

from jipsa_rag_benchmark.runner import TargetLogCollector


def test_target_log_collector_parses_ingest_stage_event(tmp_path: Path) -> None:
    collector = TargetLogCollector(
        run_id="run-001",
        output_path=tmp_path / "target.log",
    )

    event = collector._parse_stage_event(
        "{"
        '"timestamp":"2026-07-30T01:00:00.000+00:00",'
        '"event":"document_embedding_completed",'
        '"request_id":"11111111-1111-1111-1111-111111111111",'
        '"file_idx":1590001,'
        '"file_type":"pdf",'
        '"stage":"embedding",'
        '"duration_ms":125.5,'
        '"chunk_count":10'
        "}"
    )

    assert event is not None
    assert event.run_id == "run-001"
    assert event.request_id == "11111111-1111-1111-1111-111111111111"
    assert event.file_idx == 1590001
    assert event.stage == "embedding"
    assert event.duration_ms == 125.5
    assert event.chunk_count == 10


def test_target_log_collector_ignores_non_stage_log(tmp_path: Path) -> None:
    collector = TargetLogCollector(
        run_id="run-002",
        output_path=tmp_path / "target.log",
    )

    assert collector._parse_stage_event("not-json") is None
    assert (
        collector._parse_stage_event(
            '{"event":"http_request_completed","status_code":200}'
        )
        is None
    )
