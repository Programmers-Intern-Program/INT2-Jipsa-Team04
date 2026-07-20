package com.jipsa.user;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface UsersInformationRepository extends JpaRepository<UsersInformation, Long> {

    /** GET /api/v1/users/me — 삭제되지 않은(Del=false) 프로필 정보를 userId로 조회한다. */
    Optional<UsersInformation> findByUsersIdAndDelFalse(Long usersId);
}
