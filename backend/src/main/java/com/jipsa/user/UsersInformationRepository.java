package com.jipsa.user;

import org.springframework.data.jpa.repository.JpaRepository;

/** 3단계에서는 가입 시 저장(save)만 필요해 커스텀 쿼리는 두지 않는다. */
public interface UsersInformationRepository extends JpaRepository<UsersInformation, Long> {
}
