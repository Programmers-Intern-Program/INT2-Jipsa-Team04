"""실제 Local RAG MySQL 데이터베이스 연결을 통합 테스트한다."""

import asyncio

from jipsa_rag.infrastructure.database.session import (
    check_database_connection,
    close_database,
)


def test_local_rag_database_connection() -> None:
    """테스트 환경의 Local RAG MySQL에 연결하여 SELECT 1을 실행한다."""

    async def verify_connection() -> None:
        try:
            # 특정 테이블이나 데이터를 변경하지 않고
            # MySQL 서버 연결 가능 여부만 확인한다.
            await check_database_connection()
        finally:
            # 테스트 성공 여부와 관계없이 생성된 연결 풀을 정리한다.
            await close_database()

    asyncio.run(verify_connection())