"""OCR 계층의 안전한 도메인 예외를 정의한다."""


class OcrError(Exception):
    """OCR 계층에서 발생하는 모든 예외의 기본 클래스."""


class OcrDependencyUnavailableError(OcrError):
    """EasyOCR, PyTorch 또는 이미지 디코더가 설치되지 않은 경우의 예외."""


class OcrGpuUnavailableError(OcrError):
    """GPU 필수 정책인데 CUDA 장치를 사용할 수 없는 경우의 예외."""


class OcrModelUnavailableError(OcrError):
    """오프라인 모델 파일이 없고 자동 다운로드도 비활성화된 경우의 예외."""


class OcrImageDecodeError(OcrError):
    """추출한 이미지 바이트를 OCR 입력 배열로 디코딩하지 못한 경우의 예외."""


class OcrRecognitionError(OcrError):
    """OCR 엔진의 실제 추론 호출에 실패한 경우의 예외."""


class OcrTimeoutError(OcrError, TimeoutError):
    """격리된 OCR worker가 단일 이미지 제한 시간 안에 응답하지 못한 경우의 예외.

    ``TimeoutError``도 함께 상속하여 기존 ``OcrDocumentEnricher``의 단일 이미지
    timeout 부분 실패 처리와 호환된다. 동시에 ``OcrError`` 계층에도 속하므로 OCR
    전용 예외를 처리하는 다른 호출자에서도 안전하게 분류할 수 있다.
    """
