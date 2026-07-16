package com.jipsa.auth;

import com.jipsa.auth.google.GoogleIdTokenValidator;
import com.jipsa.auth.google.GoogleOAuthClient;
import com.jipsa.auth.google.GoogleTokenResponse;
import com.jipsa.auth.google.GoogleUserInfo;
import com.jipsa.user.UserFindOrCreateResult;
import com.jipsa.user.UserService;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * 구글 로그인 흐름을 조립(orchestration)하는 서비스.
 *
 * <p>1~4단계에서 이미 만들어 둔 컴포넌트들을 <b>정해진 순서대로 연결만</b> 한다.
 * 새로운 인증 로직·상태 검사·토큰 로직을 여기서 만들지 않는다:
 * <ol>
 *   <li>{@link GoogleOAuthClient#exchangeAuthorizationCode} — authorization code → Google 토큰 응답</li>
 *   <li>{@link GoogleIdTokenValidator#validate} — id_token 검증 → {@link GoogleUserInfo}</li>
 *   <li>{@link UserService#findOrCreate} — 내부 사용자 find-or-create</li>
 *   <li>{@link LoginTokenService#issueTokens} — 자체 Access/Refresh 토큰 발급 → {@link LoginResult}</li>
 * </ol>
 *
 * <p>각 단계에서 나오는 예외(GoogleAuthException=401, AccountLoginBlockedException=403 등)는
 * 임의로 catch/변환하지 않고 그대로 상위로 전파해 {@code GlobalExceptionHandler}가 처리하게 한다.
 * authorizationCode·id_token·access/refresh 토큰·{@link LoginResult}는 로그에 출력하지 않는다.
 *
 * <p><b>로그인 흐름은 트랜잭션 없음:</b> {@link #loginWithGoogle}에는 {@code @Transactional}을
 * 두지 않는다 — find-or-create의 동시 최초 로그인 경합 복구가 {@link UserService} 내부
 * 경계(REQUIRES_NEW)에 의존하기 때문이다. 반면 토큰 재발급({@link #refreshAccessToken})은
 * {@code Last_Used_At} 갱신과 상태 검사 실패 시 롤백을 한 단위로 묶기 위해 <b>그 메서드에만</b>
 * {@code @Transactional}을 둔다.
 */
@Service
public class AuthService {

    private final GoogleOAuthClient googleOAuthClient;
    private final GoogleIdTokenValidator googleIdTokenValidator;
    private final UserService userService;
    private final LoginTokenService loginTokenService;
    private final RefreshTokenService refreshTokenService;
    private final JwtService jwtService;

    public AuthService(GoogleOAuthClient googleOAuthClient,
                       GoogleIdTokenValidator googleIdTokenValidator,
                       UserService userService,
                       LoginTokenService loginTokenService,
                       RefreshTokenService refreshTokenService,
                       JwtService jwtService) {
        this.googleOAuthClient = googleOAuthClient;
        this.googleIdTokenValidator = googleIdTokenValidator;
        this.userService = userService;
        this.loginTokenService = loginTokenService;
        this.refreshTokenService = refreshTokenService;
        this.jwtService = jwtService;
    }

    /**
     * authorization code 하나로 구글 로그인을 완료하고 자체 토큰을 발급한다.
     *
     * @param authorizationCode React가 Google에서 받아 전달한 authorization code
     * @return accessToken·refreshToken·isNewUser를 담은 {@link LoginResult}
     */
    public LoginResult loginWithGoogle(String authorizationCode) {
        // 1) authorization code → 구글 토큰 응답 (HTTP 교환)
        GoogleTokenResponse tokenResponse = googleOAuthClient.exchangeAuthorizationCode(authorizationCode);
        // 2) id_token 검증 → 신뢰할 수 있는 구글 사용자 정보
        GoogleUserInfo googleUserInfo = googleIdTokenValidator.validate(tokenResponse.idToken());
        // 3) 내부 사용자 find-or-create (없으면 신규 생성)
        UserFindOrCreateResult userResult = userService.findOrCreate(googleUserInfo);
        // 4) 자체 Access/Refresh 토큰 발급 → 최종 로그인 결과
        return loginTokenService.issueTokens(userResult);
    }

    /**
     * Refresh Token으로 새 Access Token만 재발급한다(rotation 없음, 새 Refresh Token 미발급).
     *
     * <p>흐름: (1) {@link RefreshTokenService#validateAndTouch} — 원문을 SHA-256 해시로 조회·
     * 폐기·만료 검사 후 {@code Last_Used_At} 갱신 → 소유 userId, (2) {@link UserService#verifyLoginable}
     * — 계정이 로그인 가능(ACTIVE, del=false)한지 검사, (3) {@link JwtService#generateToken}
     * — 새 Access Token 발급.
     *
     * <p><b>이 메서드에만 {@code @Transactional}을 둔다.</b> {@code validateAndTouch}의
     * {@code Last_Used_At} 갱신이 같은 트랜잭션에 참여하므로, 이후 {@code verifyLoginable}가
     * {@code AccountLoginBlockedException}(403)을 던지면 갱신까지 함께 롤백된다.
     * Refresh Token 원문은 로그·예외 메시지에 출력하지 않는다.
     *
     * @param rawRefreshToken 클라이언트가 보낸 Refresh Token 원문
     * @return 새로 발급한 Access Token을 담은 {@link AccessTokenResponse}
     */
    @Transactional
    public AccessTokenResponse refreshAccessToken(String rawRefreshToken) {
        Long userId = refreshTokenService.validateAndTouch(rawRefreshToken);
        userService.verifyLoginable(userId);
        return new AccessTokenResponse(jwtService.generateToken(userId));
    }

    /**
     * Refresh Token을 폐기해 로그아웃 처리한다(하이브리드 멱등 정책, {@link RefreshTokenService#revoke} 참고).
     *
     * <p>정상·만료 토큰은 {@code Revoked_At}/{@code Revoked_Reason="LOGOUT"}을 기록하고, 이미 폐기된
     * 토큰은 기존 값을 보존하며 no-op로 성공 처리한다. 존재하지 않거나 위조된 토큰은
     * {@code UnauthorizedException}(401)으로 전파된다. 계정 상태는 검사하지 않는다 —
     * 정지/탈퇴 계정도 자기 세션을 폐기할 수 있어야 한다.
     *
     * <p><b>이 메서드에만 {@code @Transactional}을 둔다.</b> {@code revoke}의 폐기 갱신이 관리 엔티티
     * dirty checking으로 이 트랜잭션 커밋 시 flush된다. Refresh Token 행은 삭제하지 않으며,
     * Access Token 블랙리스트도 두지 않는다(기존 Access Token은 만료 전까지 유효). 원문은 로그에 출력하지 않는다.
     *
     * @param rawRefreshToken 클라이언트가 보낸 Refresh Token 원문
     */
    @Transactional
    public void logout(String rawRefreshToken) {
        refreshTokenService.revoke(rawRefreshToken);
    }
}
