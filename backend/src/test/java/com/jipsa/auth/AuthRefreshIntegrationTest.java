package com.jipsa.auth;

import com.jipsa.user.AccountLoginBlockedException;
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
 * 토큰 재발급의 DB 관측이 필요한 요구사항을 실제 컨텍스트로 검증한다:
 * Last_Used_At 갱신/롤백, Refresh_Tokens 행 수 불변(새 Refresh Token 미발급),
 * 새 Access Token의 userId, 그리고 {@code POST /api/v1/auth/refresh}의 permitAll.
 *
 * <p><b>비-트랜잭션 테스트다</b>(클래스/메서드에 {@code @Transactional} 없음). 그래야
 * {@code AuthService.refreshAccessToken}의 트랜잭션이 실제로 커밋/롤백되어 DB에 반영되고,
 * 테스트가 그 결과를 새 조회로 관측할 수 있다 — 테스트 트랜잭션이 서비스 롤백을 가리지 않는다.
 *
 * <p>정리는 FK 순서를 지킨다: 자식({@code Refresh_Tokens})을 먼저 지우고
 * 부모({@code OAuth_Connections}, {@code Users})를 지운다. 테스트마다 고유한 토큰 원문(UUID)과
 * 새로 INSERT한 사용자를 써서 잔여 데이터 간섭을 없앤다.
 */
@SpringBootTest
@AutoConfigureMockMvc
class AuthRefreshIntegrationTest {

    @Autowired private AuthService authService;
    @Autowired private JwtService jwtService;
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
    private RefreshToken seedToken(Long userId, String raw, LocalDateTime expiresAt) {
        RefreshToken t = new RefreshToken();
        t.setUsersId(userId);
        t.setTokenHash(RefreshTokenService.sha256Hex(raw));
        t.setExpiresAt(expiresAt);
        // lastUsedAt = null (미사용 상태로 시작)
        return refreshTokensRepository.save(t);
    }

    @Test
    void 정상_재발급시_새_AccessToken_userId일치_LastUsedAt갱신_행수불변() {
        Long userId = seedUser("ACTIVE", false);
        String raw = UUID.randomUUID().toString();
        Long tokenId = seedToken(userId, raw, LocalDateTime.now().plusDays(1)).getId();
        long countBefore = refreshTokensRepository.count();

        AccessTokenResponse response = authService.refreshAccessToken(raw);

        // 새 Access Token: 비어있지 않고, 검증 시 시드한 userId와 role(기본값 USERS)을 돌려준다.
        assertThat(response.accessToken()).isNotBlank();
        JwtPrincipal principal = jwtService.validateAndGetPrincipal(response.accessToken()).orElseThrow();
        assertThat(principal.userId()).isEqualTo(userId);
        assertThat(principal.role()).isEqualTo("USERS");

        // Last_Used_At 갱신(커밋됨) — 새 조회로 관측.
        RefreshToken after = refreshTokensRepository.findById(tokenId).orElseThrow();
        assertThat(after.getLastUsedAt()).isNotNull();
        assertThat(after.getRevokedAt()).isNull();     // 폐기하지 않음(회전 없음)

        // 행 수 불변 & 새 Refresh Token 미발급 — 같은 해시 1행 그대로.
        assertThat(refreshTokensRepository.count()).isEqualTo(countBefore);
        assertThat(refreshTokensRepository.findByTokenHash(RefreshTokenService.sha256Hex(raw))).isPresent();
    }

    @Test
    void 로그인불가_계정이면_403이고_LastUsedAt갱신이_롤백된다() {
        Long userId = seedUser("SUSPENDED", false);   // ACTIVE 아님 → 차단 대상
        String raw = UUID.randomUUID().toString();
        Long tokenId = seedToken(userId, raw, LocalDateTime.now().plusDays(1)).getId();

        assertThatThrownBy(() -> authService.refreshAccessToken(raw))
                .isInstanceOf(AccountLoginBlockedException.class);

        // 트랜잭션이 롤백되어 Last_Used_At는 여전히 null(=갱신 롤백) — 새 조회로 관측.
        RefreshToken after = refreshTokensRepository.findById(tokenId).orElseThrow();
        assertThat(after.getLastUsedAt()).isNull();
    }

    @Test
    void del된_사용자도_403이고_LastUsedAt갱신이_롤백된다() {
        Long userId = seedUser("ACTIVE", true);        // del=true → 차단 대상
        String raw = UUID.randomUUID().toString();
        Long tokenId = seedToken(userId, raw, LocalDateTime.now().plusDays(1)).getId();

        assertThatThrownBy(() -> authService.refreshAccessToken(raw))
                .isInstanceOf(AccountLoginBlockedException.class);

        RefreshToken after = refreshTokensRepository.findById(tokenId).orElseThrow();
        assertThat(after.getLastUsedAt()).isNull();
    }

    @Test
    void refresh_엔드포인트는_Authorization_없이도_도달한다_permitAll() throws Exception {
        Long userId = seedUser("ACTIVE", false);
        String raw = UUID.randomUUID().toString();
        seedToken(userId, raw, LocalDateTime.now().plusDays(1));

        // Authorization 헤더 없음 → permitAll이 아니면 컨트롤러 도달 전 401. 여기서는 200이어야 한다.
        mockMvc.perform(post("/api/v1/auth/refresh")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"refreshToken\":\"" + raw + "\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.success").value(true))
                .andExpect(jsonPath("$.data.accessToken").isNotEmpty());
    }

    @Test
    void 위조_토큰은_Authorization_없이_도달해_비즈니스_401을_낸다() throws Exception {
        // permitAll이라 컨트롤러까지 도달하고, 서비스가 던진 UnauthorizedException이 401(비즈니스)로 매핑된다.
        mockMvc.perform(post("/api/v1/auth/refresh")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"refreshToken\":\"" + UUID.randomUUID() + "\"}"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.success").value(false))
                .andExpect(jsonPath("$.error.code").value("UNAUTHORIZED"));
    }
}
