package com.jipsa.user;

import com.jipsa.admin.AdminUserProjection;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;

public interface UsersRepository extends JpaRepository<Users, Long> {
    // Spring Data generates all the basic CRUD (save, findById, ...) for you.
    // We'll add finders here as features need them.

    /**
     * GET /api/v1/admin/users — 사용자 목록 + 문서 수를 JOIN/GROUP BY 하나로 집계해서 가져온다
     * (사용자 수만큼 개별 COUNT 쿼리가 나가는 N+1을 피하기 위함).
     * countQuery를 별도로 준 이유: GROUP BY가 섞인 쿼리는 Page가 total count를 자동으로
     * 유도할 수 없어서(그룹 개수 != 원본 row 개수) 명시가 필요하다.
     * ORDER BY를 쿼리 안에 직접 명시한 이유: Pageable의 Sort를 그대로 두면 Spring Data가
     * "order by <속성명>"을 별칭 검증 없이 그대로 덧붙이는데, 이 프로젝션 쿼리처럼 별칭
     * 기반 select절과 섞이면 예기치 않은 SQL이 나갈 수 있어 호출부(AdminService)에서
     * Sort 없는 Pageable을 넘기고 정렬은 여기서 고정한다.
     */
    @Query(value = "select u.id as userId, u.role as role, u.status as status, u.del as del, "
            + "u.createdAt as createdAt, count(f.id) as documentCount "
            + "from Users u left join File f on f.usersId = u.id and f.deletedAt is null "
            + "group by u.id, u.role, u.status, u.del, u.createdAt "
            + "order by u.createdAt desc",
            countQuery = "select count(u) from Users u")
    Page<AdminUserProjection> findAllWithDocumentCount(Pageable pageable);
}
