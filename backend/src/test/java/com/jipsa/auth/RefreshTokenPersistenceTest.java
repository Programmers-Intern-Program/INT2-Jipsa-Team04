package com.jipsa.auth;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.data.jpa.test.autoconfigure.DataJpaTest;

import java.time.LocalDateTime;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * RefreshToken 엔티티 ↔ Refresh_Tokens 컬럼 매핑을 H2로 검증한다.
 * (EntityMappingSmokeTest와 동일한 전제: @DataJpaTest는 엔티티 매핑으로 H2 스키마를
 *  생성하므로 실제 db/init DDL과 100% 동일 검증은 아니다 — 매핑 정합성 확인 목적.)
 */
@DataJpaTest
class RefreshTokenPersistenceTest {

    @Autowired
    private RefreshTokensRepository repository;

    @Test
    void 저장하면_단일PK가_생성되고_타임스탬프가_채워진다() {
        RefreshToken token = new RefreshToken();
        token.setUsersId(1L);
        token.setTokenHash("hash-abc-123");
        token.setExpiresAt(LocalDateTime.now().plusDays(14));

        RefreshToken saved = repository.save(token);

        assertThat(saved.getId()).isNotNull();            // Refresh_Tokens_IDX (IDENTITY)
        assertThat(saved.getCreatedAt()).isNotNull();
        assertThat(saved.getUpdatedAt()).isNotNull();
        assertThat(saved.getRevokedAt()).isNull();        // 발급 시점엔 미폐기
        assertThat(saved.getRevokedReason()).isNull();
    }

    @Test
    void tokenHash로_조회할_수_있다() {
        RefreshToken token = new RefreshToken();
        token.setUsersId(2L);
        token.setTokenHash("hash-lookup-xyz");
        token.setExpiresAt(LocalDateTime.now().plusDays(14));
        repository.save(token);

        assertThat(repository.findByTokenHash("hash-lookup-xyz"))
                .isPresent()
                .get()
                .extracting(RefreshToken::getUsersId)
                .isEqualTo(2L);
    }
}
