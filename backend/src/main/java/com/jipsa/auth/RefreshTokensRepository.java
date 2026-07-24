package com.jipsa.auth;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface RefreshTokensRepository extends JpaRepository<RefreshToken, Long> {

    /**
     * 저장된 해시로 Refresh Token 행을 조회한다. 다음 단계(Refresh 재발급/Logout)에서 사용한다.
     * 이번 4단계에서는 발급/저장({@code save})만 수행한다.
     */
    Optional<RefreshToken> findByTokenHash(String tokenHash);

    /** 관리자 권한 변경 시 방어적 폐기용 — 대상 사용자의 아직 폐기되지 않은 토큰만 가져온다. */
    List<RefreshToken> findByUsersIdAndRevokedAtIsNull(Long usersId);
}
