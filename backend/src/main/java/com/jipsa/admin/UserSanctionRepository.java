package com.jipsa.admin;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Collection;
import java.util.List;
import java.util.Optional;

public interface UserSanctionRepository extends JpaRepository<UserSanction, Long> {

    /** GET /api/v1/admin/users/{id}/sanctions — 최신순 제재 이력. */
    List<UserSanction> findByUsersIdOrderByStartedAtDesc(Long usersId);

    /**
     * POST /api/v1/admin/users/{id}/unsuspend — 해제 대상이 되는 활성 "정지" 제재 1건.
     * sanctionType을 SUSPENDABLE_TYPES(TEMP_SUSPEND/PERMANENT_SUSPEND)로 제한하는 이유:
     * ACCOUNT_DELETE도 UserSanction 기본값상 Sanction_Status가 ACTIVE로 생성되는데, 타입
     * 제한 없이 조회하면 삭제 이력을 "해제"해버려 Status만 ACTIVE로 되돌아가고 Del은 그대로
     * true로 남는 모순된 상태가 될 수 있다.
     */
    Optional<UserSanction> findFirstByUsersIdAndSanctionTypeInAndSanctionStatusOrderByStartedAtDesc(
            Long usersId, Collection<SanctionType> sanctionTypes, SanctionStatus sanctionStatus);
}
