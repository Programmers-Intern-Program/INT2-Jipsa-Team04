package com.jipsa.auth;

import com.jipsa.common.exception.UnauthorizedException;
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

    /** 로그아웃으로 폐기된 토큰의 사유. {@code Refresh_Tokens.Revoked_Reason}에 저장한다. */
    static final String REVOKED_REASON_LOGOUT = "LOGOUT";

    /** 관리자 권한 변경으로 방어적 폐기된 토큰의 사유. {@link #revokeAllForUser} 참고. */
    public static final String REVOKED_REASON_ROLE_CHANGED = "ROLE_CHANGED";

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

    /**
     * Refresh Token 원문을 검증하고 사용자 식별자를 반환한다. 회전(rotation)은 하지 않는다.
     *
     * <p>원문을 SHA-256 해시해 {@code Token_Hash}로 조회하고, 폐기({@code Revoked_At})·
     * 만료({@code Expires_At})를 검사한다. 만료 기준은 <b>현재 시각과 같거나 이전이면 만료</b>
     * ({@code !expiresAt.isAfter(now)})다. 검증에 성공하면 조회한 <b>관리 엔티티</b>의
     * {@code Last_Used_At}를 갱신한다 — dirty checking으로 호출자 트랜잭션 커밋 시에만 flush된다.
     *
     * <p><b>반드시 호출자(예: {@code AuthService.refreshAccessToken})의 트랜잭션 안에서
     * 호출되어야 한다.</b> 그래야 {@code Last_Used_At} 갱신이 그 트랜잭션과 함께 커밋/롤백된다.
     * 원문은 예외 메시지·로그에 절대 출력하지 않는다.
     *
     * @param rawRefreshToken 클라이언트가 보낸 Refresh Token 원문
     * @return 토큰 소유자 (Users.Users_IDX)
     * @throws UnauthorizedException 토큰이 없거나(위조/오타 포함), 폐기되었거나, 만료된 경우
     */
    Long validateAndTouch(String rawRefreshToken) {
        RefreshToken token = refreshTokensRepository.findByTokenHash(sha256Hex(rawRefreshToken))
                .orElseThrow(() -> new UnauthorizedException("유효하지 않은 리프레시 토큰입니다."));
        if (token.getRevokedAt() != null) {
            throw new UnauthorizedException("폐기된 리프레시 토큰입니다.");
        }
        if (!token.getExpiresAt().isAfter(LocalDateTime.now())) {   // 현재와 같거나 이전이면 만료
            throw new UnauthorizedException("만료된 리프레시 토큰입니다.");
        }
        token.setLastUsedAt(LocalDateTime.now());                   // 관리 엔티티 → 커밋 시 flush
        return token.getUsersId();
    }

    /**
     * Refresh Token 원문을 검증하고 로그아웃 처리(폐기)한다. 하이브리드 멱등 정책을 따른다.
     *
     * <p>원문을 SHA-256 해시해 {@code Token_Hash}로 조회한 뒤, 상태별로 다음과 같이 처리한다:
     * <ol>
     *   <li>미존재/위조 — 조회 실패 시 {@link UnauthorizedException}(401). 폐기할 세션이 없다.</li>
     *   <li>이미 폐기됨({@code Revoked_At != null}) — <b>기존 값을 덮어쓰지 않고</b> no-op로 반환한다(멱등).</li>
     *   <li>만료됨 — 죽은 토큰이라도 {@code Revoked_At}/{@code Revoked_Reason}을 기록하고 정상 반환한다.</li>
     *   <li>정상 — {@code Revoked_At=현재시각}, {@code Revoked_Reason="LOGOUT"} 기록.</li>
     * </ol>
     *
     * <p>로그인 가능 여부(계정 상태)는 검사하지 않는다 — 정지/탈퇴 계정도 자기 세션은 폐기할 수 있어야 한다.
     * 조회한 <b>관리 엔티티</b>를 수정하므로 dirty checking으로 호출자 트랜잭션 커밋 시에만 flush된다.
     * Refresh Token 행은 삭제하지 않는다.
     *
     * <p><b>반드시 호출자(예: {@code AuthService.logout})의 트랜잭션 안에서 호출되어야 한다.</b>
     * 그래야 폐기 갱신이 그 트랜잭션과 함께 커밋된다. 원문·해시는 예외 메시지·로그에 출력하지 않는다.
     *
     * @param rawRefreshToken 클라이언트가 보낸 Refresh Token 원문
     * @throws UnauthorizedException 토큰이 존재하지 않거나 위조된 경우
     */
    void revoke(String rawRefreshToken) {
        RefreshToken token = refreshTokensRepository.findByTokenHash(sha256Hex(rawRefreshToken))
                .orElseThrow(() -> new UnauthorizedException("유효하지 않은 리프레시 토큰입니다."));
        if (token.getRevokedAt() != null) {
            return;   // 이미 폐기됨 — 기존 Revoked_At/Revoked_Reason 보존(멱등 no-op)
        }
        // 정상·만료 모두 동일하게 폐기 기록 (만료 토큰도 200으로 폐기 이력을 남긴다)
        token.setRevokedAt(LocalDateTime.now());
        token.setRevokedReason(REVOKED_REASON_LOGOUT);   // 관리 엔티티 → 커밋 시 flush
    }

    /**
     * 대상 사용자의 활성(미폐기) Refresh Token을 전부 폐기한다.
     *
     * <p>인가 판단 자체는 {@code JwtAuthenticationFilter}가 요청마다 최신 role로 재검증하므로
     * 이 폐기가 없어도 권한 변경은 이미 안전하게 반영된다 — 이 메서드는 그 재검증 로직에 버그가
     * 있거나 우회되는 경우를 대비한 방어적 이중 안전장치일 뿐이다({@code AdminService.updateRole}에서
     * 호출). 이미 만료됐거나 이미 폐기된 토큰은 건드리지 않는다(멱등).
     *
     * <p>호출자({@code AdminService.updateRole})의 트랜잭션 안에서 호출되어야 한다 — 관리
     * 엔티티 dirty checking으로 그 트랜잭션 커밋 시에만 flush된다.
     *
     * @param userId 대상 사용자 (Users.Users_IDX)
     * @param reason {@code Revoked_Reason}에 기록할 사유
     */
    @Transactional
    public void revokeAllForUser(Long userId, String reason) {
        LocalDateTime now = LocalDateTime.now();
        refreshTokensRepository.findByUsersIdAndRevokedAtIsNull(userId)
                .forEach(token -> {
                    token.setRevokedAt(now);
                    token.setRevokedReason(reason);
                });
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
