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
            @Value("${JWT_EXPIRATION_MS:86400000}") long validityMs   // default: 24h
    ) {
        // HS256 needs a >=256-bit (32-byte) key. A proper `openssl rand -base64 48`
        // value is ~64 UTF-8 bytes, comfortably above the minimum.
        this.key = Keys.hmacShaKeyFor(secret.getBytes(StandardCharsets.UTF_8));
        this.validityMs = validityMs;
    }

    /** Issue a signed token whose subject is the user's id. */
    public String generateToken(Long userId) {
        long now = System.currentTimeMillis();
        return Jwts.builder()
                .subject(String.valueOf(userId))
                .issuedAt(new Date(now))
                .expiration(new Date(now + validityMs))
                .signWith(key)
                .compact();
    }

    /** Verify a token; return the user id if valid, or empty if invalid/expired/tampered. */
    public Optional<Long> validateAndGetUserId(String token) {
        try {
            Claims claims = Jwts.parser()
                    .verifyWith(key)
                    .build()
                    .parseSignedClaims(token)
                    .getPayload();
            return Optional.of(Long.valueOf(claims.getSubject()));
        } catch (JwtException | NumberFormatException e) {
            return Optional.empty();   // bad signature, expired, malformed, etc.
        }
    }
}
