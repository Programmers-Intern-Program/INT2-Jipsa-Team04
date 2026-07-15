package com.jipsa.admin;

import java.time.LocalDateTime;

/**
 * GET /api/v1/admin/users 목록 조회용 Spring Data 인터페이스 프로젝션.
 * UsersRepository.findAllWithDocumentCount()의 JPQL select절 별칭과 getter 이름이 매칭된다.
 * Users-File LEFT JOIN + GROUP BY 집계 쿼리 하나로 문서 수까지 한 번에 가져와서
 * 사용자 수만큼 COUNT 쿼리가 추가로 나가는 N+1을 피한다.
 */
public interface AdminUserProjection {
    Long getUserId();
    String getRole();
    String getStatus();
    boolean getDel();
    LocalDateTime getCreatedAt();
    Long getDocumentCount();
}
