package com.jipsa.admin;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface UserSanctionRepository extends JpaRepository<UserSanction, Long> {

    /** GET /api/v1/admin/users/{id}/sanctions — 최신순 제재 이력. */
    List<UserSanction> findByUsersIdOrderByStartedAtDesc(Long usersId);

    /** POST /api/v1/admin/users/{id}/unsuspend — 해제 대상이 되는 활성 제재 1건. */
    Optional<UserSanction> findFirstByUsersIdAndSanctionStatusOrderByStartedAtDesc(
            Long usersId, SanctionStatus sanctionStatus);
}
