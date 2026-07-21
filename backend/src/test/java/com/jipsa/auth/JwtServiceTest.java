package com.jipsa.auth;

import org.junit.jupiter.api.Test;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * JwtService(Access Token 전용)의 발급/검증 왕복과 만료 처리를 검증한다.
 * Spring 컨텍스트 없이 생성자를 직접 호출해 결정론적으로 테스트한다.
 */
class JwtServiceTest {

    private static final String SECRET = "test-secret-0123456789-abcdefghij-0123456789";
    private static final long ACCESS_MS = 2_700_000L;   // 45분

    @Test
    void 발급한_토큰은_같은_userId와_role로_검증된다() {
        JwtService jwtService = new JwtService(SECRET, ACCESS_MS);

        String token = jwtService.generateToken(42L, "ADMIN");

        assertThat(jwtService.validateAndGetPrincipal(token))
                .contains(new JwtPrincipal(42L, "ADMIN"));
    }

    @Test
    void 변조된_토큰은_거부된다() {
        JwtService jwtService = new JwtService(SECRET, ACCESS_MS);
        String token = jwtService.generateToken(42L, "USERS");

        assertThat(jwtService.validateAndGetPrincipal(token + "tampered")).isEmpty();
    }

    @Test
    void 다른_키로_서명검증하면_거부된다() {
        String token = new JwtService(SECRET, ACCESS_MS).generateToken(42L, "USERS");
        JwtService otherKey = new JwtService("another-secret-0123456789-abcdefghij-xyz", ACCESS_MS);

        assertThat(otherKey.validateAndGetPrincipal(token)).isEmpty();
    }

    @Test
    void 만료된_토큰은_거부된다() {
        // 만료를 과거로 설정(음수 만료)해 즉시 만료된 토큰을 만든다.
        JwtService expiring = new JwtService(SECRET, -60_000L);
        String expired = expiring.generateToken(42L, "USERS");

        assertThat(expiring.validateAndGetPrincipal(expired)).isEmpty();
    }

    @Test
    void 형식이_잘못된_토큰은_거부된다() {
        JwtService jwtService = new JwtService(SECRET, ACCESS_MS);

        Optional<JwtPrincipal> result = jwtService.validateAndGetPrincipal("not-a-jwt");

        assertThat(result).isEmpty();
    }
}
