package com.jipsa.auth;

import com.jipsa.common.exception.UnauthorizedException;
import com.jipsa.user.OAuthConnectionsRepository;
import com.jipsa.user.Users;
import com.jipsa.user.UsersRepository;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

import java.time.LocalDateTime;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 로그아웃의 DB 관측이 필요한 요구사항을 실제 컨텍스트로 검증한다:
 * Revoked_At/Revoked_Reason 커밋, 만료 토큰 폐기, 이미 폐기된 토큰 멱등(값 보존),
 * 정지/탈퇴 계정 로그아웃 성공, Refresh_Tokens 행 수 불변,
 * 그리고 {@code POST /api/v1/auth/logout}의 permitAll과 위조 토큰 401.
 *
 * <p><b>비-트랜잭션 테스트다</b>(클래스/메서드에 {@code @Transactional} 없음). 그래야
 * {@code AuthService.logout}의 트랜잭션이 실제로 커밋되어 DB에 반영되고, 테스트가 그 결과를
 * 새 조회로 관측할 수 있다 — 테스트 트랜잭션이 서비스 커밋을 가리지 않는다.
 *
 * <p>정리는 FK 순서를 지킨다: 자식({@code Refresh_Tokens})을 먼저 지우고
 * 부모({@code OAuth_Connections}, {@code Users})를 지운다. 테스트마다 고유한 토큰 원문(UUID)과
 * 새로 INSERT한 사용자를 써서 잔여 데이터 간섭을 없앤다.
 */
@SpringBootTest
@AutoConfigureMockMvc
class AuthLogoutIntegrationTest {

    @Autowired private AuthService authService;
    @Autowired private RefreshTokensRepository refreshTokensRepository;
    @Autowired private UsersRepository usersRepository;
    @Autowired private OAuthConnectionsRepository oauthConnectionsRepository;
    @Autowired private MockMvc mockMvc;

    @AfterEach
    void cleanUp() {
        // FK 순서: Refresh_Tokens(자식) → OAuth_Connections → Users(부모)
        refreshTokensRepository.deleteAll();
        oauthConnectionsRepository.deleteAll();
        usersRepository.deleteAll();
    }

    /** 지정 상태의 사용자를 새로 INSERT하고 id를 반환한다. */
    private Long seedUser(String status, boolean del) {
        Users u = new Users();
        u.setStatus(status);
        u.setDel(del);
        return usersRepository.save(u).getId();
    }

    /** raw 원문의 SHA-256 해시로 Refresh Token 행을 INSERT한다(원문은 저장하지 않음). */
    private RefreshToken seedToken(Long userId, String raw, LocalDateTime expiresAt, LocalDateTime revokedAt) {
        RefreshToken t = new RefreshToken();
        t.setUsersId(userId);
        t.setTokenHash(RefreshTokenService.sha256Hex(raw));
        t.setExpiresAt(expiresAt);
        t.setRevokedAt(revokedAt);
        return refreshTokensRepository.save(t);
    }

    @Test
    void 정상_로그아웃시_RevokedAt와_LOGOUT_사유가_커밋되고_행수불변() {
        Long userId = seedUser("ACTIVE", false);
        String raw = UUID.randomUUID().toString();
        Long tokenId = seedToken(userId, raw, LocalDateTime.now().plusDays(1), null).getId();
        long countBefore = refreshTokensRepository.count();

        authService.logout(raw);

        // Revoked_At/Revoked_Reason 커밋됨 — 새 조회로 관측.
        RefreshToken after = refreshTokensRepository.findById(tokenId).orElseThrow();
        assertThat(after.getRevokedAt()).isNotNull();
        assertThat(after.getRevokedReason()).isEqualTo("LOGOUT");

        // 행 삭제 안 됨 — 행 수 불변, 같은 해시 1행 그대로.
        assertThat(refreshTokensRepository.count()).isEqualTo(countBefore);
        assertThat(refreshTokensRepository.findByTokenHash(RefreshTokenService.sha256Hex(raw))).isPresent();
    }

    @Test
    void 만료된_토큰도_200이고_폐기_기록이_커밋된다() {
        Long userId = seedUser("ACTIVE", false);
        String raw = UUID.randomUUID().toString();
        Long tokenId = seedToken(userId, raw, LocalDateTime.now().minusSeconds(1), null).getId();

        authService.logout(raw);   // 만료여도 예외 없이 폐기

        RefreshToken after = refreshTokensRepository.findById(tokenId).orElseThrow();
        assertThat(after.getRevokedAt()).isNotNull();
        assertThat(after.getRevokedReason()).isEqualTo("LOGOUT");
    }

    @Test
    void 이미_폐기된_토큰_재로그아웃시_기존_값을_보존한다_멱등() {
        Long userId = seedUser("ACTIVE", false);
        String raw = UUID.randomUUID().toString();
        LocalDateTime originalRevokedAt = LocalDateTime.now().minusDays(2).withNano(0);
        RefreshToken seeded = seedToken(userId, raw, LocalDateTime.now().plusDays(1), originalRevokedAt);
        seeded.setRevokedReason("PREVIOUS_REASON");
        refreshTokensRepository.save(seeded);
        Long tokenId = seeded.getId();

        authService.logout(raw);   // 멱등 no-op

        RefreshToken after = refreshTokensRepository.findById(tokenId).orElseThrow();
        assertThat(after.getRevokedAt()).isEqualTo(originalRevokedAt);        // 덮어쓰지 않음
        assertThat(after.getRevokedReason()).isEqualTo("PREVIOUS_REASON");   // 보존
    }

    @Test
    void SUSPENDED_사용자도_로그아웃에_성공한다() {
        Long userId = seedUser("SUSPENDED", false);
        String raw = UUID.randomUUID().toString();
        Long tokenId = seedToken(userId, raw, LocalDateTime.now().plusDays(1), null).getId();

        authService.logout(raw);   // 상태 검사 없음 → 정지 계정도 폐기 가능

        RefreshToken after = refreshTokensRepository.findById(tokenId).orElseThrow();
        assertThat(after.getRevokedAt()).isNotNull();
        assertThat(after.getRevokedReason()).isEqualTo("LOGOUT");
    }

    @Test
    void WITHDRAWN_del된_사용자도_로그아웃에_성공한다() {
        Long userId = seedUser("WITHDRAWN", true);
        String raw = UUID.randomUUID().toString();
        Long tokenId = seedToken(userId, raw, LocalDateTime.now().plusDays(1), null).getId();

        authService.logout(raw);

        RefreshToken after = refreshTokensRepository.findById(tokenId).orElseThrow();
        assertThat(after.getRevokedAt()).isNotNull();
        assertThat(after.getRevokedReason()).isEqualTo("LOGOUT");
    }

    @Test
    void 미존재_위조_토큰_로그아웃은_Unauthorized() {
        assertThatThrownBy(() -> authService.logout(UUID.randomUUID().toString()))
                .isInstanceOf(UnauthorizedException.class);
    }

    @Test
    void logout_엔드포인트는_Authorization_없이도_도달한다_permitAll() throws Exception {
        Long userId = seedUser("ACTIVE", false);
        String raw = UUID.randomUUID().toString();
        seedToken(userId, raw, LocalDateTime.now().plusDays(1), null);

        // Authorization 헤더 없음 → permitAll이 아니면 컨트롤러 도달 전 401. 여기서는 200이어야 한다.
        mockMvc.perform(post("/api/v1/auth/logout")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"refreshToken\":\"" + raw + "\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.success").value(true))
                .andExpect(jsonPath("$.data.success").value(true));
    }

    @Test
    void 위조_토큰은_Authorization_없이_도달해_비즈니스_401을_낸다() throws Exception {
        // permitAll이라 컨트롤러까지 도달하고, 서비스가 던진 UnauthorizedException이 401(비즈니스)로 매핑된다.
        mockMvc.perform(post("/api/v1/auth/logout")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"refreshToken\":\"" + UUID.randomUUID() + "\"}"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.success").value(false))
                .andExpect(jsonPath("$.error.code").value("UNAUTHORIZED"));
    }
}
