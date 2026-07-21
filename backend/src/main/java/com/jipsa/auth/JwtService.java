package com.jipsa.auth;

import io.jsonwebtoken.Claims;
import io.jsonwebtoken.JwtException;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import javax.crypto.SecretKey;
import java.nio.charset.StandardCharsets;
import java.util.Date;
import java.util.Optional;

@Service
public class JwtService {

    private final SecretKey key;
    private final long validityMs;

    public JwtService(
            // Real value comes from the JWT_SECRET env var (.env). The default is a
            // dev-only fallback so tests/local runs without .env still start. NEVER
            // rely on the default for anything real — always set JWT_SECRET.
            @Value("${JWT_SECRET:dev-only-insecure-change-me-0123456789-abcdefghij}") String secret,
            // Access Token 전용 만료. OS/.env 환경변수를 직접 읽는다(application.yaml 미사용).
            // 기본값 2700000ms = 45분. Refresh Token 만료는 RefreshTokenService가 별도로 관리한다.
            @Value("${JWT_ACCESS_EXPIRATION_MS:2700000}") long validityMs
    ) {
        // HS256 needs a >=256-bit (32-byte) key. A proper `openssl rand -base64 48`
        // value is ~64 UTF-8 bytes, comfortably above the minimum.
        this.key = Keys.hmacShaKeyFor(secret.getBytes(StandardCharsets.UTF_8));
        this.validityMs = validityMs;
    }

    /** Issue a signed token whose subject is the user's id and which carries their role as a claim. */
    public String generateToken(Long userId, String role) {
        long now = System.currentTimeMillis();
        return Jwts.builder()
                .subject(String.valueOf(userId))
                .claim("role", role)
                .issuedAt(new Date(now))
                .expiration(new Date(now + validityMs))
                .signWith(key)
                .compact();
    }

    /**
     * Verify a token; return the user id + role if valid, or empty if invalid/expired/tampered.
     *
     * <p>The role reflects whatever was true at issue time — it is not re-checked against the
     * Users table here, so a role change only takes effect once the caller gets a new token
     * (re-login or refresh).
     */
    public Optional<JwtPrincipal> validateAndGetPrincipal(String token) {
        try {
            Claims claims = Jwts.parser()
                    .verifyWith(key)
                    .build()
                    .parseSignedClaims(token)
                    .getPayload();
            Long userId = Long.valueOf(claims.getSubject());
            String role = claims.get("role", String.class);
            return Optional.of(new JwtPrincipal(userId, role));
        } catch (JwtException | NumberFormatException e) {
            return Optional.empty();   // bad signature, expired, malformed, etc.
        }
    }
}
