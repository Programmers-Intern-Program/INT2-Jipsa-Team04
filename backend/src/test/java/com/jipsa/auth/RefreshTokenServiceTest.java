package com.jipsa.auth;

import com.jipsa.common.exception.UnauthorizedException;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Captor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.LocalDateTime;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * RefreshTokenService의 발급 규칙과 재발급 검증(validateAndTouch)을 검증한다:
 * 원문은 랜덤이고, DB에는 원문이 아닌 SHA-256 해시만 저장되며, 만료는 14일로 설정된다.
 * 재발급 검증은 해시 조회·폐기·만료 판정과 Last_Used_At 갱신을 확인한다.
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
        // issue() 테스트에서만 쓰이는 스텁 — validateAndTouch 테스트에서는 미사용이라 lenient.
        lenient().when(repository.save(any(RefreshToken.class))).thenAnswer(inv -> inv.getArgument(0));
    }

    /** 해시 조회로 반환할 유효한(미폐기) 토큰 엔티티를 만든다. */
    private RefreshToken tokenWith(Long usersId, LocalDateTime expiresAt, LocalDateTime revokedAt) {
        RefreshToken t = new RefreshToken();
        t.setUsersId(usersId);
        t.setExpiresAt(expiresAt);
        t.setRevokedAt(revokedAt);
        return t;
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

    // --- validateAndTouch (재발급 검증) ---

    @Test
    void 원문이_아니라_SHA256_해시로_조회한다() {
        String raw = "raw-refresh-token";
        when(repository.findByTokenHash(any()))
                .thenReturn(Optional.of(tokenWith(7L, LocalDateTime.now().plusDays(1), null)));

        service.validateAndTouch(raw);

        ArgumentCaptor<String> hashCaptor = ArgumentCaptor.forClass(String.class);
        verify(repository).findByTokenHash(hashCaptor.capture());
        assertThat(hashCaptor.getValue())
                .isEqualTo(RefreshTokenService.sha256Hex(raw))   // 결정적 해시로 조회
                .isNotEqualTo(raw)                               // 원문으로 조회하지 않음
                .hasSize(64);
    }

    @Test
    void 유효한_토큰이면_usersId를_반환하고_LastUsedAt를_갱신한다() {
        RefreshToken token = tokenWith(42L, LocalDateTime.now().plusDays(1), null);
        assertThat(token.getLastUsedAt()).isNull();
        when(repository.findByTokenHash(any())).thenReturn(Optional.of(token));

        Long userId = service.validateAndTouch("raw");

        assertThat(userId).isEqualTo(42L);
        assertThat(token.getLastUsedAt()).isNotNull();   // 관리 엔티티 dirty → 커밋 시 flush
    }

    @Test
    void 미존재_또는_위조_토큰이면_Unauthorized() {
        when(repository.findByTokenHash(any())).thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.validateAndTouch("forged"))
                .isInstanceOf(UnauthorizedException.class);
    }

    @Test
    void 폐기된_토큰이면_Unauthorized_그리고_LastUsedAt_미갱신() {
        RefreshToken revoked = tokenWith(7L, LocalDateTime.now().plusDays(1), LocalDateTime.now().minusHours(1));
        when(repository.findByTokenHash(any())).thenReturn(Optional.of(revoked));

        assertThatThrownBy(() -> service.validateAndTouch("raw"))
                .isInstanceOf(UnauthorizedException.class);
        assertThat(revoked.getLastUsedAt()).isNull();
    }

    @Test
    void 만료된_토큰이면_Unauthorized() {
        RefreshToken expired = tokenWith(7L, LocalDateTime.now().minusSeconds(1), null);
        when(repository.findByTokenHash(any())).thenReturn(Optional.of(expired));

        assertThatThrownBy(() -> service.validateAndTouch("raw"))
                .isInstanceOf(UnauthorizedException.class);
        assertThat(expired.getLastUsedAt()).isNull();
    }

    @Test
    void 검증_실패시_토큰을_저장하지_않는다_새_RefreshToken_미발급() {
        when(repository.findByTokenHash(any())).thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.validateAndTouch("raw"))
                .isInstanceOf(UnauthorizedException.class);
        verify(repository, never()).save(any());   // 재발급 흐름은 새 토큰을 INSERT하지 않는다
    }

    // --- revoke (로그아웃) ---

    @Test
    void 로그아웃도_원문이_아니라_SHA256_해시로_조회한다() {
        String raw = "raw-refresh-token";
        when(repository.findByTokenHash(any()))
                .thenReturn(Optional.of(tokenWith(7L, LocalDateTime.now().plusDays(1), null)));

        service.revoke(raw);

        ArgumentCaptor<String> hashCaptor = ArgumentCaptor.forClass(String.class);
        verify(repository).findByTokenHash(hashCaptor.capture());
        assertThat(hashCaptor.getValue())
                .isEqualTo(RefreshTokenService.sha256Hex(raw))   // 결정적 해시로 조회
                .isNotEqualTo(raw)                               // 원문으로 조회하지 않음
                .hasSize(64);
    }

    @Test
    void 정상_토큰이면_RevokedAt와_LOGOUT_사유를_기록한다() {
        RefreshToken token = tokenWith(7L, LocalDateTime.now().plusDays(1), null);
        when(repository.findByTokenHash(any())).thenReturn(Optional.of(token));

        service.revoke("raw");

        assertThat(token.getRevokedAt()).isNotNull();            // 관리 엔티티 dirty → 커밋 시 flush
        assertThat(token.getRevokedReason()).isEqualTo("LOGOUT");
    }

    @Test
    void 만료된_토큰도_폐기_기록하고_예외를_던지지_않는다() {
        RefreshToken expired = tokenWith(7L, LocalDateTime.now().minusSeconds(1), null);
        when(repository.findByTokenHash(any())).thenReturn(Optional.of(expired));

        service.revoke("raw");   // 만료여도 200 경로 — 예외 없음

        assertThat(expired.getRevokedAt()).isNotNull();
        assertThat(expired.getRevokedReason()).isEqualTo("LOGOUT");
    }

    @Test
    void 이미_폐기된_토큰이면_기존_값을_덮어쓰지_않는다_멱등() {
        LocalDateTime originalRevokedAt = LocalDateTime.now().minusDays(1);
        RefreshToken alreadyRevoked = tokenWith(7L, LocalDateTime.now().plusDays(1), originalRevokedAt);
        alreadyRevoked.setRevokedReason("PREVIOUS_REASON");
        when(repository.findByTokenHash(any())).thenReturn(Optional.of(alreadyRevoked));

        service.revoke("raw");   // no-op

        assertThat(alreadyRevoked.getRevokedAt()).isEqualTo(originalRevokedAt);   // 보존
        assertThat(alreadyRevoked.getRevokedReason()).isEqualTo("PREVIOUS_REASON");
    }

    @Test
    void 미존재_또는_위조_토큰이면_로그아웃도_Unauthorized() {
        when(repository.findByTokenHash(any())).thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.revoke("forged"))
                .isInstanceOf(UnauthorizedException.class);
    }

    @Test
    void 로그아웃은_토큰_행을_삭제하지_않는다_새_토큰도_발급하지_않는다() {
        RefreshToken token = tokenWith(7L, LocalDateTime.now().plusDays(1), null);
        when(repository.findByTokenHash(any())).thenReturn(Optional.of(token));

        service.revoke("raw");

        verify(repository, never()).delete(any());
        verify(repository, never()).deleteById(any());
        verify(repository, never()).save(any());   // 폐기는 dirty checking, 새 INSERT 없음
    }
}
