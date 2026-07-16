package com.jipsa.auth;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Captor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.LocalDateTime;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * RefreshTokenService의 발급 규칙을 검증한다:
 * 원문은 랜덤이고, DB에는 원문이 아닌 SHA-256 해시만 저장되며, 만료는 14일로 설정된다.
 */
@ExtendWith(MockitoExtension.class)
class RefreshTokenServiceTest {

    private static final long REFRESH_MS = 1_209_600_000L;   // 14일

    @Mock
    private RefreshTokensRepository repository;

    @Captor
    private ArgumentCaptor<RefreshToken> tokenCaptor;

    private RefreshTokenService service;

    @org.junit.jupiter.api.BeforeEach
    void setUp() {
        service = new RefreshTokenService(repository, REFRESH_MS);
        when(repository.save(any(RefreshToken.class))).thenAnswer(inv -> inv.getArgument(0));
    }

    @Test
    void 발급하면_원문이_아니라_SHA256_해시가_저장된다() {
        String raw = service.issue(7L);

        verify(repository).save(tokenCaptor.capture());
        RefreshToken saved = tokenCaptor.getValue();

        assertThat(raw).isNotBlank();
        assertThat(saved.getUsersId()).isEqualTo(7L);
        // 저장된 해시 == 반환 원문의 SHA-256, 그리고 원문 자체는 저장되지 않는다.
        assertThat(saved.getTokenHash()).isEqualTo(RefreshTokenService.sha256Hex(raw));
        assertThat(saved.getTokenHash()).isNotEqualTo(raw);
        assertThat(saved.getTokenHash()).hasSize(64);   // SHA-256 hex
    }

    @Test
    void 만료시각은_약_14일_뒤로_설정된다() {
        LocalDateTime before = LocalDateTime.now();

        service.issue(7L);

        verify(repository).save(tokenCaptor.capture());
        LocalDateTime expiresAt = tokenCaptor.getValue().getExpiresAt();

        assertThat(expiresAt).isAfter(before.plusDays(13));
        assertThat(expiresAt).isBefore(before.plusDays(15));
    }

    @Test
    void 매_발급마다_서로_다른_토큰과_해시가_생성된다() {
        String raw1 = service.issue(7L);
        String raw2 = service.issue(7L);

        verify(repository, times(2)).save(tokenCaptor.capture());
        var saved = tokenCaptor.getAllValues();

        assertThat(raw1).isNotEqualTo(raw2);
        assertThat(saved.get(0).getTokenHash()).isNotEqualTo(saved.get(1).getTokenHash());
    }
}
