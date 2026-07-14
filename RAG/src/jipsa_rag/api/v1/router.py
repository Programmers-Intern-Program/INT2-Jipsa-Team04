"""API v1에 속하는 하위 엔드포인트 라우터를 통합한다."""

from fastapi import APIRouter

from jipsa_rag.api.v1.endpoints.health import router as health_router

# main.py에 등록할 API v1 통합 라우터이다.
router = APIRouter()

# 헬스 체크 관련 엔드포인트를 API v1 라우터에 등록한다.
router.include_router(health_router)
