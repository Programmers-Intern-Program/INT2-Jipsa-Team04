package com.jipsa.user;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface OAuthConnectionsRepository extends JpaRepository<OAuthConnection, Long> {

    // Used by the (Increment 2) find-or-create login logic:
    // "is there already an active connection for this Google user?"
    Optional<OAuthConnection> findByProviderAndProviderUserIdAndDelFalse(
            String provider, String providerUserId);

    // Increment 3: del 상태와 무관하게 이 (provider, sub) 연결 이력이 있었는지 확인.
    // 활성 연결이 없는데도 여기서 true면 = 탈퇴(del=true) 이력이 있는 계정 →
    // 자동 재가입/재활성화 없이 로그인을 차단하기 위해 사용한다.
    boolean existsByProviderAndProviderUserId(String provider, String providerUserId);
}
