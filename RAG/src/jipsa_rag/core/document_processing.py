"""다중 형식 문서 파싱, 이미지 추출, OCR 및 구조화 청킹 설정을 관리한다.

문서 처리 설정은 애플리케이션 공통 설정과 분리한다. 이미지 추출과 OCR은
원본 파일 크기뿐 아니라 압축 해제 크기, 이미지 픽셀 수, GPU 메모리 및 외부
렌더러 실행 시간을 함께 제한해야 하기 때문이다.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from jipsa_rag.core.config import resolve_env_file, resolve_environment


class DocumentProcessingSettings(BaseSettings):
    """구조화 청킹과 이미지/OCR 처리의 안전 한계를 정의한다."""

    # 기존 CharacterTextChunker 기본값과 같게 유지한다. OCR 텍스트도 최종적으로
    # 동일 청커를 통과하므로 기존 결정적 Chunk ID 생성 규칙을 재사용한다.
    chunk_size_chars: int = Field(default=1_000, ge=1, le=100_000)
    chunk_overlap_chars: int = Field(default=200, ge=0, le=99_999)

    # 현재 운영 중인 결정적 Chunk ID 계약 버전은 2다. OCR 도입에 따른 결과 변경은
    # Hybrid Parser의 parser_version=2.0.0이 ChunkingContext에 포함되어 구분하므로,
    # 전역 색인 계약 자체를 불필요하게 증가시키지 않는다.
    index_version: int = Field(default=2, ge=1, le=2_147_483_647)

    # OOXML ZIP 중앙 디렉터리 한계다. 다운로드 압축 파일 크기와 별개로 압축 해제
    # 후 자원 사용량을 제한한다.
    ooxml_max_member_count: int = Field(default=20_000, ge=10, le=100_000)
    ooxml_max_total_uncompressed_bytes: int = Field(
        default=512 * 1024 * 1024,
        ge=1024 * 1024,
        le=4 * 1024 * 1024 * 1024,
    )
    ooxml_max_member_uncompressed_bytes: int = Field(
        default=128 * 1024 * 1024,
        ge=1024,
        le=2 * 1024 * 1024 * 1024,
    )
    ooxml_max_compression_ratio: float = Field(default=200.0, gt=1.0, le=10_000.0)

    # ---------------------------------------------------------
    # 이미지 추출 및 렌더링
    # ---------------------------------------------------------
    image_extraction_enabled: bool = True
    image_max_count_per_document: int = Field(default=300, ge=1, le=5_000)
    image_max_bytes: int = Field(
        default=25 * 1024 * 1024,
        ge=1024,
        le=256 * 1024 * 1024,
    )
    # 압축 문서와 렌더 페이지를 합친 문서 단위 메모리 상한이다. 단일 이미지
    # 한계만으로는 작은 이미지 수백 장이 누적되는 경우를 막을 수 없으므로
    # 추출기와 OCR 후보 선택 단계에서 모두 교차 검증한다.
    image_max_total_bytes: int = Field(
        default=256 * 1024 * 1024,
        ge=1024 * 1024,
        le=2 * 1024 * 1024 * 1024,
    )
    image_max_pixels: int = Field(default=40_000_000, ge=10_000, le=250_000_000)

    # OCR 가치가 낮은 작은 아이콘, 배지, 로고 및 장식 요소를 기본 제외한다.
    # 페이지 전체 렌더와 차트 렌더는 충분히 큰 이미지이므로 같은 기준을 통과한다.
    image_decorative_filter_enabled: bool = True
    image_min_bytes: int = Field(default=1_024, ge=0, le=10 * 1024 * 1024)
    image_min_width_px: int = Field(default=64, ge=1, le=4_096)
    image_min_height_px: int = Field(default=32, ge=1, le=4_096)
    image_min_area_pixels: int = Field(default=4_096, ge=1, le=16_777_216)
    image_max_aspect_ratio: float = Field(default=12.0, ge=1.0, le=100.0)
    image_hash_dedup_enabled: bool = True

    # 스캔 PDF 탐지는 텍스트가 매우 적고 페이지 대부분을 이미지가 차지하는지를
    # 함께 확인한다. 텍스트가 전혀 없는 페이지는 이미지 면적과 무관하게 후보가 된다.
    scan_pdf_text_threshold_chars: int = Field(default=24, ge=0, le=10_000)
    scan_pdf_image_coverage_ratio: float = Field(default=0.60, ge=0.0, le=1.0)
    scan_pdf_render_dpi: int = Field(default=200, ge=72, le=400)

    # PowerPoint 2024와 Excel 2024를 pywin32 COM으로 제어한다. PPTX 차트와
    # SmartArt는 Shape.Export, XLSX 차트는 Chart.Export로 대상 객체만 직접 PNG
    # 출력하므로 전체 문서를 PDF로 변환하는 중간 단계를 사용하지 않는다.
    office_rendering_enabled: bool = True
    office_rendering_provider: Literal["microsoft_office_com"] = "microsoft_office_com"
    office_com_require_interactive_session: bool = True
    office_render_max_concurrency: int = Field(default=1, ge=1, le=1)
    office_render_timeout_seconds: float = Field(default=120.0, gt=0.0, le=900.0)
    office_render_dpi: int = Field(default=160, ge=72, le=300)

    # ---------------------------------------------------------
    # OCR
    # ---------------------------------------------------------
    ocr_enabled: bool = True
    ocr_languages_csv: str = "ko,en"
    ocr_gpu: bool = True
    ocr_gpu_required: bool = True
    ocr_device: str = "cuda:0"
    ocr_model_storage_directory: Path | None = None
    ocr_model_download_enabled: bool = False
    ocr_max_concurrency: int = Field(default=2, ge=1, le=16)
    # 단일 이미지 OCR이 GPU 오류나 비정상 입력으로 무기한 점유하지 않도록
    # 추론 호출별 제한 시간을 적용한다. 초 단위이며 timeout은 해당 이미지만 실패로
    # 기록하고 나머지 텍스트·이미지 처리는 계속 진행한다.
    ocr_timeout_seconds: float = Field(default=45.0, gt=0.0, le=600.0)
    ocr_document_timeout_seconds: float = Field(default=600.0, gt=0.0, le=3_600.0)
    ocr_min_confidence: float = Field(default=0.35, ge=0.0, le=1.0)
    ocr_context_max_chars: int = Field(default=700, ge=0, le=10_000)
    ocr_text_max_chars_per_image: int = Field(default=20_000, ge=1, le=200_000)

    model_config = SettingsConfigDict(
        env_prefix="JIPSA_RAG_",
        case_sensitive=False,
        extra="ignore",
        env_file_encoding="utf-8",
        populate_by_name=True,
    )

    @property
    def ocr_languages(self) -> tuple[str, ...]:
        """쉼표 구분 언어 목록을 중복 없는 EasyOCR 언어 코드로 반환한다."""

        languages: list[str] = []
        for value in self.ocr_languages_csv.split(","):
            normalized = value.strip()
            if normalized and normalized not in languages:
                languages.append(normalized)

        if not languages:
            raise ValueError("OCR 언어 목록에는 하나 이상의 언어 코드가 필요합니다.")

        return tuple(languages)

    @model_validator(mode="after")
    def validate_cross_field_limits(self) -> Self:
        """서로 연관된 청킹, 압축, Office 및 GPU 설정의 관계를 검증한다."""

        if self.chunk_overlap_chars >= self.chunk_size_chars:
            raise ValueError("청크 overlap은 청크 크기보다 작아야 합니다.")

        if self.ooxml_max_member_uncompressed_bytes > self.ooxml_max_total_uncompressed_bytes:
            raise ValueError("OOXML 단일 엔트리 한계는 전체 해제 크기 한계보다 클 수 없습니다.")

        if self.image_max_bytes > self.image_max_total_bytes:
            raise ValueError("단일 이미지 한계는 문서 전체 이미지 한계보다 클 수 없습니다.")

        if self.image_min_area_pixels > self.image_max_pixels:
            raise ValueError("최소 이미지 면적은 최대 픽셀 한계보다 클 수 없습니다.")

        if self.office_render_max_concurrency != 1:
            raise ValueError("Microsoft Office COM 렌더링 동시성은 1이어야 합니다.")

        if self.ocr_timeout_seconds > self.ocr_document_timeout_seconds:
            raise ValueError("단일 OCR 제한 시간은 문서 OCR 제한 시간보다 클 수 없습니다.")

        if self.ocr_gpu_required and not self.ocr_gpu:
            raise ValueError("OCR GPU 필수 설정에서는 ocr_gpu를 비활성화할 수 없습니다.")

        # property 접근으로 빈 언어 설정을 모델 생성 시점에 즉시 검증한다.
        _ = self.ocr_languages
        return self


@lru_cache(maxsize=1)
def get_document_processing_settings() -> DocumentProcessingSettings:
    """현재 실행 환경의 문서 처리 설정 객체를 생성하고 재사용한다."""

    environment = resolve_environment()
    return DocumentProcessingSettings(_env_file=resolve_env_file(environment))
