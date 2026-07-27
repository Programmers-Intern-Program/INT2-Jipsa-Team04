"""OCR 엔진과 문서 보강 단계가 공유하는 불변 결과 모델을 정의한다."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OcrTextLine:
    """OCR 엔진이 인식한 한 줄의 텍스트와 신뢰도."""

    text: str
    confidence: float
    order: int


@dataclass(frozen=True, slots=True)
class OcrRecognitionResult:
    """단일 이미지의 OCR 인식 결과."""

    lines: tuple[OcrTextLine, ...]
    engine_name: str
    languages: tuple[str, ...]
    device: str

    @property
    def text(self) -> str:
        """인식 순서를 보존하여 줄 단위 텍스트를 결합한다."""

        return "\n".join(line.text for line in self.lines if line.text)

    @property
    def mean_confidence(self) -> float:
        """인식된 줄의 산술 평균 신뢰도를 반환한다."""

        if not self.lines:
            return 0.0
        return sum(line.confidence for line in self.lines) / len(self.lines)
