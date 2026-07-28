"""EasyOCR CUDA 추론을 종료 가능한 공유 worker process에서 실행한다.

``asyncio.to_thread()``로 시작한 동기 CUDA 작업은 coroutine 취소만으로 중단할 수
없다. 이 모듈은 EasyOCR 추론을 ``spawn`` 방식의 전용 자식 프로세스에 격리하고,
단일 이미지 timeout 또는 상위 문서 task 취소가 발생하면 해당 worker를 실제로
종료한 뒤 새 worker로 교체한다.

애플리케이션은 ``EasyOcrEngine`` 인스턴스 하나를 FastAPI lifespan 동안 공유한다.
따라서 worker pool과 동시성 제한은 요청별이 아니라 Local RAG 프로세스 전체에
적용된다. 각 worker는 ``EasyOcrRuntime`` 하나를 소유하고 Reader와 CUDA 모델을
최초 추론 시 한 번만 로드하여 이후 작업에 재사용한다.
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from multiprocessing.context import BaseContext
from multiprocessing.process import BaseProcess
from typing import Final, Protocol, cast
from uuid import uuid4

from jipsa_rag.core.document_processing import DocumentProcessingSettings
from jipsa_rag.infrastructure.document.images.models import ExtractedDocumentImage
from jipsa_rag.infrastructure.ocr.easyocr import EasyOcrRuntime
from jipsa_rag.infrastructure.ocr.exceptions import (
    OcrDependencyUnavailableError,
    OcrError,
    OcrGpuUnavailableError,
    OcrImageDecodeError,
    OcrModelUnavailableError,
    OcrRecognitionError,
    OcrTimeoutError,
)
from jipsa_rag.infrastructure.ocr.models import OcrRecognitionResult

logger = logging.getLogger(__name__)

_EASYOCR_ENGINE_NAME: Final[str] = "EASYOCR_CUDA"
_PROCESS_JOIN_TIMEOUT_SECONDS: Final[float] = 2.0
_PROCESS_KILL_JOIN_TIMEOUT_SECONDS: Final[float] = 2.0
_EXCHANGE_THREAD_SETTLE_TIMEOUT_SECONDS: Final[float] = 2.0
_IPC_POLL_INTERVAL_SECONDS: Final[float] = 0.1
_WORKER_START_TIMEOUT_SECONDS: Final[float] = 30.0


@dataclass(frozen=True, slots=True)
class OcrWorkerRecognizeRequest:
    """부모 프로세스가 worker에 전달하는 단일 이미지 OCR 요청."""

    request_id: str
    content: bytes


@dataclass(frozen=True, slots=True)
class OcrWorkerShutdownRequest:
    """유휴 worker에 정상 종료를 요청하는 제어 메시지."""


@dataclass(frozen=True, slots=True)
class OcrWorkerReady:
    """spawn 초기화가 끝나 요청을 받을 수 있음을 알리는 startup 메시지."""

    worker_pid: int


@dataclass(frozen=True, slots=True)
class OcrWorkerSuccess:
    """worker가 반환하는 정상 OCR 결과."""

    request_id: str
    result: OcrRecognitionResult
    worker_pid: int


@dataclass(frozen=True, slots=True)
class OcrWorkerFailure:
    """민감한 예외 원문을 제거한 worker 실패 결과."""

    request_id: str
    error_type: str
    worker_pid: int


type OcrWorkerResponse = OcrWorkerSuccess | OcrWorkerFailure


class OcrWorkerConnection(Protocol):
    """Windows와 POSIX Pipe endpoint가 공통으로 제공하는 최소 IPC 계약."""

    def send(self, obj: object) -> None:
        """직렬화 가능한 객체 하나를 반대편 endpoint로 전송한다."""

        ...

    def recv(self) -> object:
        """반대편 endpoint가 전송한 객체 하나를 수신한다."""

        ...

    def poll(self, timeout: float = 0.0) -> bool:
        """제한 시간 안에 읽을 응답이 준비됐는지 확인한다."""

        ...

    def close(self) -> None:
        """현재 프로세스가 소유한 Pipe endpoint handle을 닫는다."""

        ...


class OcrPipeFactory(Protocol):
    """multiprocessing context의 ``Pipe`` 호출 계약."""

    def __call__(self, duplex: bool = True) -> tuple[object, object]:
        """부모와 자식이 사용할 Pipe endpoint 한 쌍을 생성한다."""

        ...


class OcrProcessFactory(Protocol):
    """multiprocessing context의 ``Process`` 호출 계약."""

    def __call__(
        self,
        *,
        target: object,
        args: tuple[object, ...],
        name: str,
        daemon: bool,
    ) -> BaseProcess:
        """지정한 target을 실행할 spawn worker process를 생성한다."""

        ...


class OcrProcessContext(Protocol):
    """spawn context에서 worker와 양방향 Pipe를 생성하는 최소 계약."""

    Pipe: OcrPipeFactory
    Process: OcrProcessFactory


class OcrWorkerTarget(Protocol):
    """테스트 대역으로 교체할 수 있는 OCR worker 진입점 계약."""

    def __call__(
        self,
        connection: OcrWorkerConnection,
        settings_payload: dict[str, object],
    ) -> None:
        """IPC 연결과 직렬화된 문서 처리 설정으로 worker loop를 실행한다."""

        ...


@dataclass(slots=True)
class _WorkerSlot:
    """pool에서 재사용하는 단일 worker process와 부모 IPC 연결."""

    slot_index: int
    generation: int
    process: BaseProcess
    connection: OcrWorkerConnection


class EasyOcrEngine:
    """애플리케이션 전체에서 공유하는 종료 가능한 EasyOCR process pool.

    ``ocr_max_concurrency``만큼의 worker process를 생성하며, 각 worker는 동시에 한
    이미지만 처리한다. 이 수가 실제 CUDA 모델 복제 수와 동시 추론 수의 전역 상한이다.
    단일 GPU의 VRAM 여유가 작다면 설정값을 1로 사용해야 한다.

    worker는 첫 OCR 호출 때 지연 생성된다. 텍스트 전용 요청과 GPU가 없는 일반 단위
    테스트에서는 불필요한 자식 프로세스를 만들지 않는다. ``close()``는 worker가 한 번도
    시작되지 않은 경우에도 안전하며 FastAPI lifespan 종료 단계에서 반드시 호출한다.
    """

    def __init__(
        self,
        settings: DocumentProcessingSettings,
        *,
        process_context: BaseContext | None = None,
        worker_target: OcrWorkerTarget | None = None,
    ) -> None:
        self._settings = settings
        self._process_context = cast(
            OcrProcessContext,
            process_context or multiprocessing.get_context("spawn"),
        )
        self._worker_target = worker_target or _easyocr_worker_main
        self._settings_payload = cast(
            dict[str, object],
            settings.model_dump(mode="python"),
        )

        # asyncio.Lock은 생성 시점에 특정 이벤트 루프에 연결되지 않는다. 실제 start,
        # recognize, close 호출은 동일 FastAPI lifespan 이벤트 루프에서 이루어진다.
        self._lifecycle_lock = asyncio.Lock()
        self._state_lock = threading.RLock()
        self._available_workers: asyncio.Queue[_WorkerSlot] | None = None
        self._worker_slots: list[_WorkerSlot] = []
        self._started = False
        self._closing = False
        self._closed = False

    @property
    def engine_name(self) -> str:
        """저장 메타데이터에 사용할 안정적인 OCR 엔진 이름을 반환한다."""

        return _EASYOCR_ENGINE_NAME

    @property
    def worker_process_ids(self) -> tuple[int, ...]:
        """현재 살아 있는 worker PID를 안전한 진단 정보로 반환한다.

        PID는 테스트와 운영 상태 진단에만 사용하며 로그에 원본 이미지, OCR 결과 또는
        모델 경로를 포함하지 않는다.
        """

        with self._state_lock:
            return tuple(
                process_id
                for slot in self._worker_slots
                if _is_process_alive(slot.process) and (process_id := slot.process.pid) is not None
            )

    async def start(self) -> None:
        """설정된 전역 동시성 수만큼 OCR worker process를 준비한다.

        여러 요청이 첫 이미지에서 동시에 진입해도 lifecycle lock으로 pool 생성은 한 번만
        수행된다. worker는 Reader를 즉시 로드하지 않고 첫 실제 추론에서 지연 초기화한다.
        """

        async with self._lifecycle_lock:
            if self._closed or self._closing:
                raise OcrRecognitionError("OCR process engine is already closed.")
            if self._started:
                return

            slots = await asyncio.to_thread(self._spawn_worker_pool_sync)
            queue: asyncio.Queue[_WorkerSlot] = asyncio.Queue(
                maxsize=self._settings.ocr_max_concurrency
            )
            for slot in slots:
                queue.put_nowait(slot)

            with self._state_lock:
                self._worker_slots = list(slots)
                self._available_workers = queue
                self._started = True

            logger.info(
                "ocr_worker_pool_started",
                extra={
                    "ocr_worker_count": len(slots),
                    "ocr_device": self._settings.ocr_device if self._settings.ocr_gpu else "cpu",
                },
            )

    async def recognize(
        self,
        image: ExtractedDocumentImage,
    ) -> OcrRecognitionResult:
        """한 이미지 OCR을 공유 pool에서 실행하고 timeout 시 worker를 교체한다.

        상위 ``asyncio.wait_for()`` 또는 문서 전체 timeout이 이 coroutine을 취소하면
        ``CancelledError``를 받은 즉시 해당 worker process를 종료한다. 따라서 coroutine은
        실패했는데 기존 ``readtext()``가 CUDA에서 계속 실행되는 상태를 남기지 않는다.
        """

        if not self._started:
            await self.start()

        queue = self._require_available_queue()
        slot = await queue.get()
        return_slot = True
        try:
            result = await self._recognize_with_slot(slot, image)
            return result
        except asyncio.CancelledError:
            # _recognize_with_slot에서 worker 교체가 완료된 뒤 취소를 다시 전파한다.
            raise
        except Exception:
            # worker 교체 자체가 실패했다면 죽은 slot을 queue에 다시 넣지 않는다.
            if not _is_process_alive(slot.process):
                return_slot = False
            raise
        finally:
            if return_slot and not self._closing and not self._closed:
                queue.put_nowait(slot)

    async def close(self) -> None:
        """모든 OCR worker와 부모 IPC 연결을 종료한다.

        정상 종료 메시지를 먼저 전달하되, 추론 중이거나 응답하지 않는 worker는 제한 시간
        뒤 terminate/kill한다. CUDA context는 worker process 종료와 함께 운영체제가
        회수하므로 부모 프로세스에 timeout된 GPU 작업이 남지 않는다.
        """

        async with self._lifecycle_lock:
            if self._closed:
                return

            self._closing = True
            try:
                await asyncio.to_thread(self._close_worker_pool_sync)
            finally:
                with self._state_lock:
                    self._worker_slots.clear()
                    self._available_workers = None
                    self._started = False
                    self._closed = True
                    self._closing = False

            logger.info("ocr_worker_pool_closed")

    async def _recognize_with_slot(
        self,
        slot: _WorkerSlot,
        image: ExtractedDocumentImage,
    ) -> OcrRecognitionResult:
        """한 worker와 IPC를 수행하며 timeout·취소·worker crash를 복구한다."""

        request = OcrWorkerRecognizeRequest(
            request_id=uuid4().hex,
            content=bytes(image.content),
        )
        exchange_task = asyncio.create_task(
            asyncio.to_thread(
                _exchange_with_worker_sync,
                slot.connection,
                slot.process,
                request,
            )
        )

        try:
            done, _ = await asyncio.wait(
                {exchange_task},
                timeout=self._settings.ocr_timeout_seconds,
            )
            if not done:
                await self._replace_worker_after_abort(
                    slot,
                    exchange_task=exchange_task,
                    reason="timeout",
                )
                raise OcrTimeoutError("EasyOCR worker exceeded the image timeout.")

            response = exchange_task.result()
        except asyncio.CancelledError:
            await self._replace_worker_after_abort(
                slot,
                exchange_task=exchange_task,
                reason="cancelled",
            )
            raise
        except OcrTimeoutError:
            # TimeoutError는 OSError의 하위 클래스이므로 IPC 오류 처리보다 먼저
            # 분리하지 않으면 이미 교체한 worker를 다시 교체하게 된다.
            raise
        except (EOFError, OSError) as error:
            await self._replace_worker_after_abort(
                slot,
                exchange_task=exchange_task,
                reason="ipc_failure",
            )
            raise OcrRecognitionError("EasyOCR worker communication failed.") from error
        except Exception as error:
            await self._replace_worker_after_abort(
                slot,
                exchange_task=exchange_task,
                reason="worker_failure",
            )
            raise OcrRecognitionError("EasyOCR worker failed unexpectedly.") from error

        if response.request_id != request.request_id:
            await self._replace_worker_after_abort(
                slot,
                exchange_task=exchange_task,
                reason="response_mismatch",
            )
            raise OcrRecognitionError("EasyOCR worker returned a mismatched response.")

        if isinstance(response, OcrWorkerFailure):
            raise _restore_worker_error(response.error_type)
        return response.result

    async def _replace_worker_after_abort(
        self,
        slot: _WorkerSlot,
        *,
        exchange_task: asyncio.Task[OcrWorkerResponse],
        reason: str,
    ) -> None:
        """중단 불가능한 worker를 종료하고 같은 slot에 새 process를 배치한다."""

        old_process_id = slot.process.pid
        try:
            await asyncio.to_thread(self._replace_worker_sync, slot)
        finally:
            # 부모 쪽 to_thread가 닫힌 Pipe에서 빠져나오도록 짧게 기다린다. 이 대기는
            # CUDA 작업 완료를 기다리는 것이 아니라 이미 종료한 process의 IPC 정리만
            # 확인한다.
            await _settle_exchange_task(exchange_task)

        logger.warning(
            "ocr_worker_replaced",
            extra={
                "ocr_worker_slot": slot.slot_index,
                "ocr_worker_generation": slot.generation,
                "ocr_worker_previous_pid": old_process_id,
                "ocr_worker_replacement_reason": reason,
            },
        )

    def _require_available_queue(self) -> asyncio.Queue[_WorkerSlot]:
        """시작된 worker queue를 반환하고 잘못된 생명주기 상태를 거부한다."""

        with self._state_lock:
            queue = self._available_workers
            if queue is None or not self._started or self._closed or self._closing:
                raise OcrRecognitionError("OCR process engine is not available.")
            return queue

    def _spawn_worker_pool_sync(self) -> tuple[_WorkerSlot, ...]:
        """Windows spawn 규칙에 맞춰 고정 크기 worker pool을 생성한다."""

        slots: list[_WorkerSlot] = []
        try:
            for slot_index in range(self._settings.ocr_max_concurrency):
                slots.append(self._spawn_worker_sync(slot_index=slot_index, generation=1))
        except Exception:
            for slot in slots:
                _stop_worker_process(slot.process, slot.connection, graceful=False)
            raise
        return tuple(slots)

    def _spawn_worker_sync(self, *, slot_index: int, generation: int) -> _WorkerSlot:
        """부모·자식 단방향 요청/응답 Pipe와 daemon worker를 생성한다."""

        raw_parent_connection, raw_child_connection = self._process_context.Pipe(duplex=True)
        parent_connection = cast(OcrWorkerConnection, raw_parent_connection)
        child_connection = cast(OcrWorkerConnection, raw_child_connection)
        process = self._process_context.Process(
            target=self._worker_target,
            args=(child_connection, dict(self._settings_payload)),
            name=f"jipsa-rag-ocr-{slot_index}-{generation}",
            daemon=True,
        )
        try:
            process.start()
        except Exception:
            parent_connection.close()
            child_connection.close()
            raise
        finally:
            # 부모 프로세스는 child endpoint를 사용하지 않는다. Windows handle 누수를
            # 막기 위해 start 직후 부모가 보유한 복제본을 닫는다.
            with suppress(OSError):
                child_connection.close()

        try:
            _await_worker_ready_sync(process, parent_connection)
        except Exception:
            # 자식 interpreter import, 설정 역직렬화 또는 worker 초기화가 실패하면
            # 준비되지 않은 process를 pool에 등록하지 않고 handle을 즉시 회수한다.
            _stop_worker_process(process, parent_connection, graceful=False)
            raise

        return _WorkerSlot(
            slot_index=slot_index,
            generation=generation,
            process=process,
            connection=parent_connection,
        )

    def _replace_worker_sync(self, slot: _WorkerSlot) -> None:
        """timeout 또는 취소된 worker를 강제 종료하고 새 generation으로 교체한다."""

        with self._state_lock:
            _stop_worker_process(slot.process, slot.connection, graceful=False)
            if self._closing or self._closed:
                return

            replacement = self._spawn_worker_sync(
                slot_index=slot.slot_index,
                generation=slot.generation + 1,
            )
            slot.process = replacement.process
            slot.connection = replacement.connection
            slot.generation = replacement.generation

    def _close_worker_pool_sync(self) -> None:
        """현재 pool의 모든 worker를 정상 종료 후 필요 시 강제 종료한다."""

        with self._state_lock:
            slots = tuple(self._worker_slots)

        for slot in slots:
            _stop_worker_process(slot.process, slot.connection, graceful=True)


def _easyocr_worker_main(
    connection: OcrWorkerConnection,
    settings_payload: dict[str, object],
) -> None:
    """한 자식 프로세스에서 EasyOCR Reader를 재사용하는 직렬 worker loop."""

    settings = DocumentProcessingSettings.model_validate(settings_payload)
    runtime = EasyOcrRuntime(settings)
    connection.send(
        OcrWorkerReady(
            worker_pid=multiprocessing.current_process().pid or 0,
        )
    )

    try:
        while True:
            command_object = connection.recv()
            if isinstance(command_object, OcrWorkerShutdownRequest):
                return
            if not isinstance(command_object, OcrWorkerRecognizeRequest):
                # 부모와 worker 코드 버전이 불일치한 경우 안전하게 종료한다. 알 수 없는
                # 객체를 다시 직렬화하거나 그 repr를 로그에 남기지 않는다.
                return

            request = command_object
            try:
                result = runtime.recognize_content(request.content)
                response: OcrWorkerResponse = OcrWorkerSuccess(
                    request_id=request.request_id,
                    result=result,
                    worker_pid=multiprocessing.current_process().pid or 0,
                )
            except OcrError as error:
                response = OcrWorkerFailure(
                    request_id=request.request_id,
                    error_type=type(error).__name__,
                    worker_pid=multiprocessing.current_process().pid or 0,
                )
            except Exception:
                response = OcrWorkerFailure(
                    request_id=request.request_id,
                    error_type=OcrRecognitionError.__name__,
                    worker_pid=multiprocessing.current_process().pid or 0,
                )

            connection.send(response)
    except (EOFError, OSError):
        # 부모가 timeout·취소로 Pipe를 닫은 정상 종료 경로다. 원본 데이터나 IPC 오류
        # 문자열은 child stderr에 출력하지 않는다.
        return
    finally:
        with suppress(OSError):
            connection.close()


def _await_worker_ready_sync(
    process: BaseProcess,
    connection: OcrWorkerConnection,
) -> None:
    """spawn 자식이 import와 worker 초기화를 끝낼 때까지 제한 대기한다.

    준비 handshake를 마친 worker만 pool에 등록한다. 따라서 첫 OCR 요청의
    ``ocr_timeout_seconds``에는 Windows interpreter 시작과 테스트 모듈 재import
    시간이 포함되지 않는다.
    """

    deadline = time.monotonic() + _WORKER_START_TIMEOUT_SECONDS
    while True:
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0.0:
            raise OcrRecognitionError("EasyOCR worker startup timed out.")

        poll_timeout = min(_IPC_POLL_INTERVAL_SECONDS, remaining_seconds)
        if connection.poll(poll_timeout):
            startup_message = connection.recv()
            if not isinstance(startup_message, OcrWorkerReady):
                raise OcrRecognitionError("EasyOCR worker returned an invalid startup response.")
            if process.pid is not None and startup_message.worker_pid != process.pid:
                raise OcrRecognitionError("EasyOCR worker returned a mismatched startup PID.")
            return

        if not _is_process_alive(process):
            raise OcrRecognitionError("EasyOCR worker exited during startup.")


def _exchange_with_worker_sync(
    connection: OcrWorkerConnection,
    process: BaseProcess,
    request: OcrWorkerRecognizeRequest,
) -> OcrWorkerResponse:
    """부모의 보조 thread에서 blocking Pipe 송수신을 수행한다."""

    if not _is_process_alive(process):
        raise OcrRecognitionError("EasyOCR worker is not running.")

    connection.send(request)
    while True:
        # recv()를 무기한 blocking 호출하지 않는다. 부모가 timeout 또는 취소로 worker와
        # Pipe를 닫으면 이 보조 thread가 다음 poll 주기 안에 빠져나와 executor thread가
        # 누적되지 않는다. CUDA 작업 종료는 별도로 process terminate/kill이 보장한다.
        if connection.poll(_IPC_POLL_INTERVAL_SECONDS):
            response_object = connection.recv()
            if not isinstance(response_object, OcrWorkerSuccess | OcrWorkerFailure):
                raise OcrRecognitionError("EasyOCR worker returned an invalid response.")
            return response_object
        if not _is_process_alive(process):
            raise EOFError("EasyOCR worker exited before returning a response.")


def _is_process_alive(process: BaseProcess) -> bool:
    """이미 close된 process handle을 포함해 생존 여부를 예외 없이 반환한다."""

    try:
        return process.is_alive()
    except ValueError:
        # 다른 정리 경로가 process.close()를 먼저 호출한 경우다. 닫힌 handle은
        # 더 이상 실행 중인 worker로 취급하지 않는다.
        return False


def _stop_worker_process(
    process: BaseProcess,
    connection: OcrWorkerConnection,
    *,
    graceful: bool,
) -> None:
    """한 worker와 Pipe를 종료하고 OS process handle까지 회수한다."""

    try:
        if graceful and _is_process_alive(process):
            with suppress(EOFError, OSError):
                connection.send(OcrWorkerShutdownRequest())
            process.join(timeout=_PROCESS_JOIN_TIMEOUT_SECONDS)

        if _is_process_alive(process):
            process.terminate()
            process.join(timeout=_PROCESS_JOIN_TIMEOUT_SECONDS)

        if _is_process_alive(process):
            # Python 3.12의 multiprocessing Process.kill()은 Windows에서도 사용 가능하다.
            # terminate가 반환되지 않는 네이티브 CUDA 정지 상황의 마지막 방어선이다.
            process.kill()
            process.join(timeout=_PROCESS_KILL_JOIN_TIMEOUT_SECONDS)
    finally:
        with suppress(OSError):
            connection.close()
        with suppress(ValueError):
            process.close()


def _restore_worker_error(error_type: str) -> OcrError:
    """worker가 전달한 안전한 예외 종류를 부모 도메인 예외로 복원한다."""

    error_factories: dict[str, type[OcrError]] = {
        OcrDependencyUnavailableError.__name__: OcrDependencyUnavailableError,
        OcrGpuUnavailableError.__name__: OcrGpuUnavailableError,
        OcrImageDecodeError.__name__: OcrImageDecodeError,
        OcrModelUnavailableError.__name__: OcrModelUnavailableError,
        OcrRecognitionError.__name__: OcrRecognitionError,
    }
    error_class = error_factories.get(error_type, OcrRecognitionError)
    return error_class("EasyOCR worker reported a protected OCR failure.")


async def _settle_exchange_task(
    task: asyncio.Task[OcrWorkerResponse],
) -> None:
    """종료한 worker의 blocking IPC 보조 thread가 빠져나오도록 제한 대기한다."""

    if task.done():
        with suppress(Exception, asyncio.CancelledError):
            task.result()
        return

    try:
        await asyncio.wait_for(
            asyncio.shield(task),
            timeout=_EXCHANGE_THREAD_SETTLE_TIMEOUT_SECONDS,
        )
    except (TimeoutError, asyncio.CancelledError):
        # worker process와 Pipe는 이미 닫혔다. executor thread 종료 지연이 상위 요청의
        # 취소 또는 timeout 결과를 가리지 않도록 추가 예외를 외부로 전파하지 않는다.
        return
    except Exception:
        # Pipe 종료로 발생한 EOFError, OSError 등의 내부 예외도 상위 timeout 또는
        # 취소 결과를 덮어쓰지 않는다.
        return
