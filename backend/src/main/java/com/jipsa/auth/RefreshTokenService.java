package com.jipsa.auth;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.time.LocalDateTime;
import java.util.Base64;

/**
 * 자체 Refresh Token 발급 전담 서비스. Access Token(JWT)과 달리 Refresh Token은
 * <b>무상태 JWT가 아니라 SecureRandom 기반 랜덤 문자열</b>이며, DB에는 <b>원문이 아닌
 * SHA-256 해시</b>만 저장한다({@link RefreshToken} / {@code Refresh_Tokens.Token_Hash}).
 *
 * <p>이 단계에서는 발급/저장만 담당한다. Refresh 재발급·회전·폐기(Revoke)·Logout은
 * 다음 단계 소관이다. 로그인마다 새 행을 1건 INSERT하며, 기존 토큰은 건드리지 않는다.
 */
@Service
public class RefreshTokenService {

    /** 스레드 세이프. 랜덤 원문 32바이트(=256비트) 생성용. */
    private static final SecureRandom RANDOM = new SecureRandom();
    private static final int TOKEN_BYTES = 32;

    private final RefreshTokensRepository refreshTokensRepository;
    private final long refreshValidityMs;

    public RefreshTokenService(
            RefreshTokensRepository refreshTokensRepository,
            // OS/.env 환경변수를 직접 읽는다(application.yaml 미사용). 기본값 14일.
            @Value("${JWT_REFRESH_EXPIRATION_MS:1209600000}") long refreshValidityMs
    ) {
        this.refreshTokensRepository = refreshTokensRepository;
        this.refreshValidityMs = refreshValidityMs;
    }

    /**
     * 사용자에게 새 Refresh Token을 발급한다.
     *
     * <p>랜덤 원문을 만들어 그 SHA-256 해시를 저장하고, <b>원문을 반환</b>한다.
     * 원문은 응답으로만 나가고 DB에는 남지 않는다.
     *
     * @param userId 토큰 소유자 (Users.Users_IDX)
     * @return Refresh Token 원문 (base64url)
     */
    @Transactional
    public String issue(Long userId) {
        String rawToken = generateRawToken();

        RefreshToken entity = new RefreshToken();
        entity.setUsersId(userId);
        entity.setTokenHash(sha256Hex(rawToken));
        entity.setExpiresAt(LocalDateTime.now().plusNanos(refreshValidityMs * 1_000_000));
        refreshTokensRepository.save(entity);

        return rawToken;
    }

    /** SecureRandom 32바이트 → base64url(패딩 없음) 원문. */
    private static String generateRawToken() {
        byte[] bytes = new byte[TOKEN_BYTES];
        RANDOM.nextBytes(bytes);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    }

    /** 결정적 SHA-256 → 소문자 hex(64자). Token_Hash UNIQUE 조회를 위해 결정적이어야 한다. */
    static String sha256Hex(String value) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder(digest.length * 2);
            for (byte b : digest) {
                sb.append(Character.forDigit((b >> 4) & 0xF, 16));
                sb.append(Character.forDigit(b & 0xF, 16));
            }
            return sb.toString();
        } catch (NoSuchAlgorithmException e) {
            // SHA-256은 모든 JVM에 필수 — 실제로는 발생하지 않는다.
            throw new IllegalStateException("SHA-256 not available", e);
        }
    }
}
