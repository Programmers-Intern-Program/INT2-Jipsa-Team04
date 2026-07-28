"""EasyOCR worker process의 공유, timeout 종료 및 생명주기 계약을 검증한다."""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import time
from contextlib import suppress

import pytest

from jipsa_rag.core.document_processing import DocumentProcessingSettings
from jipsa_rag.infrastructure.document.images.models import (
    DocumentImageKind,
    ExtractedDocumentImage,
)
from jipsa_rag.infrastructure.ocr.exceptions import OcrRecognitionError, OcrTimeoutError
from jipsa_rag.infrastructure.ocr.models import OcrRecognitionResult, OcrTextLine
from jipsa_rag.infrastructure.ocr.process_manager import (
    EasyOcrEngine,
    OcrWorkerConnection,
    OcrWorkerFailure,
    OcrWorkerReady,
    OcrWorkerRecognizeRequest,
    OcrWorkerShutdownRequest,
    OcrWorkerSuccess,
)


def _fake_worker_main(
    connection: OcrWorkerConnection,
    settings_payload: dict[str, object],
) -> None:
    """CUDA 없이 process 교체와 pool 공유를 검증하는 blocking worker 대역."""

    del settings_payload
    worker_pid = os.getpid()
    connection.send(OcrWorkerReady(worker_pid=worker_pid))

    try:
        while True:
            command = connection.recv()
            if isinstance(command, OcrWorkerShutdownRequest):
                return
            if not isinstance(command, OcrWorkerRecognizeRequest):
                return

            if command.content == b"block":
                # asyncio.sleep이 아니라 실제 동기 blocking 작업을 사용한다. 부모가
                # coroutine만 취소해서는 이 작업이 끝나지 않으므로 process 종료 여부를
                # 정확히 검증할 수 있다.
                time.sleep(30.0)
                continue
            if command.content == b"crash":
                os._exit(7)
            if command.content == b"failure":
                connection.send(
                    OcrWorkerFailure(
                        request_id=command.request_id,
                        error_type="OcrRecognitionError",
                        worker_pid=worker_pid,
                    )
                )
                continue

            # 짧은 지연을 넣어 여러 요청이 동시에 들어왔을 때 고정 worker 수를
            # 초과하는 process가 생성되지 않는지 관찰할 수 있게 한다.
            time.sleep(0.05)
            text = f"pid:{worker_pid};payload:{command.content.decode('ascii')}"
            connection.send(
                OcrWorkerSuccess(
                    request_id=command.request_id,
                    result=OcrRecognitionResult(
                        lines=(OcrTextLine(text=text, confidence=1.0, order=0),),
                        engine_name="FAKE_PROCESS_OCR",
                        languages=("ko", "en"),
                        device="cuda:0",
                    ),
                    worker_pid=worker_pid,
                )
            )
    except (EOFError, OSError):
        return
    finally:
        with suppress(OSError):
            connection.close()


def _settings(
    *,
    maximum_concurrency: int = 1,
    image_timeout_seconds: float = 1.0,
) -> DocumentProcessingSettings:
    """실제 CUDA나 모델 파일 없이 process manager만 검증하는 설정을 반환한다."""

    return DocumentProcessingSettings(
        ocr_gpu=False,
        ocr_gpu_required=False,
        ocr_max_concurrency=maximum_concurrency,
        ocr_timeout_seconds=image_timeout_seconds,
        ocr_document_timeout_seconds=max(image_timeout_seconds, 2.0),
        _env_file=None,
    )


def _image(content: bytes) -> ExtractedDocumentImage:
    """process worker에 전달할 최소 불변 이미지 모델을 생성한다."""

    return ExtractedDocumentImage(
        image_id=f"test-{content.hex()}",
        kind=DocumentImageKind.PDF_EMBEDDED,
        content=content,
        media_type="image/png",
        extension="png",
        source_metadata={"page_number": 1, "image_index": 1},
    )


def _result_pid(result: OcrRecognitionResult) -> int:
    """fake OCR 결과에 기록된 worker PID를 정수로 읽는다."""

    text = result.lines[0].text
    prefix = text.split(";", maxsplit=1)[0]
    return int(prefix.removeprefix("pid:"))


def _active_child_pids() -> set[int]:
    """현재 테스트 부모가 관리하는 살아 있는 자식 PID 집합을 반환한다."""

    return {
        process_id
        for process in multiprocessing.active_children()
        if (process_id := process.pid) is not None
    }


@pytest.mark.asyncio
async def test_engine_reuses_same_worker_process_for_sequential_requests() -> None:
    """여러 요청이 같은 공유 worker와 Reader 생명주기를 재사용한다."""

    engine = EasyOcrEngine(
        _settings(),
        process_context=multiprocessing.get_context("spawn"),
        worker_target=_fake_worker_main,
    )
    try:
        await engine.start()
        initial_pids = engine.worker_process_ids

        first = await engine.recognize(_image(b"first"))
        second = await engine.recognize(_image(b"second"))

        assert len(initial_pids) == 1
        assert _result_pid(first) == initial_pids[0]
        assert _result_pid(second) == initial_pids[0]
        assert engine.worker_process_ids == initial_pids
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_image_timeout_terminates_blocking_worker_before_slot_reuse() -> None:
    """timeout된 blocking 작업을 process째 종료하고 새 worker로 교체한다."""

    engine = EasyOcrEngine(
        _settings(image_timeout_seconds=2.0),
        process_context=multiprocessing.get_context("spawn"),
        worker_target=_fake_worker_main,
    )
    try:
        await engine.start()
        old_pid = engine.worker_process_ids[0]

        with pytest.raises(OcrTimeoutError):
            await engine.recognize(_image(b"block"))

        replacement_pid = engine.worker_process_ids[0]
        assert replacement_pid != old_pid
        assert old_pid not in _active_child_pids()

        recovered = await engine.recognize(_image(b"recovered"))
        assert _result_pid(recovered) == replacement_pid
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_task_cancellation_terminates_worker_before_propagating_cancel() -> None:
    """문서 timeout이 coroutine을 취소해도 실제 blocking worker가 남지 않는다."""

    engine = EasyOcrEngine(
        _settings(image_timeout_seconds=10.0),
        process_context=multiprocessing.get_context("spawn"),
        worker_target=_fake_worker_main,
    )
    try:
        await engine.start()
        old_pid = engine.worker_process_ids[0]
        task = asyncio.create_task(engine.recognize(_image(b"block")))

        await asyncio.sleep(0.15)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        replacement_pid = engine.worker_process_ids[0]
        assert replacement_pid != old_pid
        assert old_pid not in _active_child_pids()

        recovered = await engine.recognize(_image(b"after-cancel"))
        assert _result_pid(recovered) == replacement_pid
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_process_pool_bounds_concurrency_across_all_requests() -> None:
    """동시 요청 수와 관계없이 process 수가 전역 설정값을 초과하지 않는다."""

    engine = EasyOcrEngine(
        _settings(maximum_concurrency=2),
        process_context=multiprocessing.get_context("spawn"),
        worker_target=_fake_worker_main,
    )
    try:
        await engine.start()
        configured_pids = set(engine.worker_process_ids)

        results = await asyncio.gather(
            *(engine.recognize(_image(f"job-{index}".encode("ascii"))) for index in range(8))
        )
        used_pids = {_result_pid(result) for result in results}

        assert len(configured_pids) == 2
        assert used_pids == configured_pids
        assert set(engine.worker_process_ids) == configured_pids
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_worker_domain_failure_keeps_healthy_process_reusable() -> None:
    """일반 OCR 오류는 process를 불필요하게 재시작하지 않고 부분 실패로 전달한다."""

    engine = EasyOcrEngine(
        _settings(),
        process_context=multiprocessing.get_context("spawn"),
        worker_target=_fake_worker_main,
    )
    try:
        await engine.start()
        worker_pid = engine.worker_process_ids[0]

        with pytest.raises(OcrRecognitionError):
            await engine.recognize(_image(b"failure"))

        assert engine.worker_process_ids == (worker_pid,)
        recovered = await engine.recognize(_image(b"after-failure"))
        assert _result_pid(recovered) == worker_pid
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_worker_process_crash_is_replaced_before_next_request() -> None:
    """native crash와 같은 비정상 종료도 죽은 slot을 반환하지 않고 복구한다."""

    engine = EasyOcrEngine(
        _settings(image_timeout_seconds=2.0),
        process_context=multiprocessing.get_context("spawn"),
        worker_target=_fake_worker_main,
    )
    try:
        await engine.start()
        old_pid = engine.worker_process_ids[0]

        with pytest.raises(OcrRecognitionError):
            await engine.recognize(_image(b"crash"))

        replacement_pid = engine.worker_process_ids[0]
        assert replacement_pid != old_pid
        assert old_pid not in _active_child_pids()

        recovered = await engine.recognize(_image(b"after-crash"))
        assert _result_pid(recovered) == replacement_pid
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_close_terminates_all_worker_processes_and_is_idempotent() -> None:
    """FastAPI shutdown에서 모든 worker와 process handle을 반복 안전하게 정리한다."""

    engine = EasyOcrEngine(
        _settings(maximum_concurrency=2),
        process_context=multiprocessing.get_context("spawn"),
        worker_target=_fake_worker_main,
    )
    await engine.start()
    worker_pids = set(engine.worker_process_ids)
    assert len(worker_pids) == 2

    await engine.close()
    await engine.close()

    assert engine.worker_process_ids == ()
    assert worker_pids.isdisjoint(_active_child_pids())
