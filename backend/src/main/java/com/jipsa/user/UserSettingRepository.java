package com.jipsa.user;

import org.springframework.data.jpa.repository.JpaRepository;

/** ID(=Users_IDX)로 조회/저장만 하면 충분해서 커스텀 쿼리는 없다. */
public interface UserSettingRepository extends JpaRepository<UserSetting, Long> {
}
