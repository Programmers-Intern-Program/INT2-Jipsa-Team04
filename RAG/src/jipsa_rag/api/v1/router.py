"""API v1에 속하는 하위 엔드포인트 라우터를 통합한다."""

from fastapi import APIRouter, Depends

from jipsa_rag.api.internal_auth import verify_rag_ingest_token
from jipsa_rag.api.v1.endpoints.chunk_search import (
    router as chunk_search_router,
)
from jipsa_rag.api.v1.endpoints.file_processing import (
    router as file_processing_router,
)
from jipsa_rag.api.v1.endpoints.health import router as health_router
from jipsa_rag.api.v1.endpoints.network_diagnostics import (
    router as network_diagnostics_router,
)
from jipsa_rag.api.v1.endpoints.rag_answer import (
    router as rag_answer_router,
)

# main.py에 등록할 API v1 통합 라우터이다.
router = APIRouter()

# 헬스 체크 엔드포인트는 내부 토큰 없이도 접근할 수 있도록 유지한다.
#
# 로컬 프로세스, 운영 모니터링 또는 컨테이너 상태 검사는
# 서비스 간 파일 인제스트 인증과 별개의 책임이다.
router.include_router(
    health_router,
)

# 네트워크 진단 응답에는 RAG 외부 주소, outbound 공인 IP와 같은
# 네트워크 구성 정보가 포함된다.
#
# 공개 Health API와 분리하고 백엔드 -> RAG 요청에 사용하는
# 내부 인증 토큰을 검증한 호출자에게만 응답한다.
router.include_router(
    network_diagnostics_router,
    dependencies=[
        Depends(verify_rag_ingest_token),
    ],
)

# 선택 참조문서 기반 답변은 사용자 문서의 검색 결과와 발췌문을
# 외부 응답에 포함하므로 반드시 내부 인증을 통과해야 한다.
#
# 라우터 통합 지점에 인증 dependency를 적용하면 향후 /rag 하위 경로가
# 추가되어도 엔드포인트별 인증 누락으로 우회되는 상황을 방지할 수 있다.
router.include_router(
    rag_answer_router,
    dependencies=[
        Depends(verify_rag_ingest_token),
    ],
)

# 관련 청크 검색은 사용자 문서 원문을 응답하므로
# 반드시 내부 인증을 통과해야 한다.
#
# 검색 엔드포인트 함수 안에서 개별적으로 인증하면 향후 같은 라우터에
# 경로가 추가될 때 dependency 누락으로 인증 우회가 발생할 수 있다.
#
# 따라서 라우터를 통합하는 시점에 공통 dependency를 적용하여
# 모든 /chunks 경로를 보호한다.
router.include_router(
    chunk_search_router,
    dependencies=[
        Depends(verify_rag_ingest_token),
    ],
)

# 기존 POST /api/v1/files/process도 POST /ingest와 동일한 파일 처리
# 파이프라인을 실행한다.
#
# 신규 /ingest 경로에만 인증을 적용하면 호출자가 기존 경로를 사용하여
# 내부 토큰 검증을 우회할 수 있으므로 파일 처리 라우터 전체에
# 동일한 내부 인증 정책을 적용한다.
router.include_router(
    file_processing_router,
    dependencies=[
        Depends(verify_rag_ingest_token),
    ],
)
