package com.jipsa.auth;

import com.jipsa.auth.google.GoogleIdTokenValidator;
import com.jipsa.auth.google.GoogleOAuthClient;
import com.jipsa.auth.google.GoogleTokenResponse;
import com.jipsa.auth.google.GoogleUserInfo;
import com.jipsa.user.UserFindOrCreateResult;
import com.jipsa.user.UserService;
import org.springframework.stereotype.Service;

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
 * <p><b>트랜잭션 없음:</b> 이 서비스에는 {@code @Transactional}을 두지 않는다 — find-or-create의
 * 동시 최초 로그인 경합 복구가 {@link UserService} 내부 경계(REQUIRES_NEW)에 의존하기 때문이다.
 */
@Service
public class AuthService {

    private final GoogleOAuthClient googleOAuthClient;
    private final GoogleIdTokenValidator googleIdTokenValidator;
    private final UserService userService;
    private final LoginTokenService loginTokenService;

    public AuthService(GoogleOAuthClient googleOAuthClient,
                       GoogleIdTokenValidator googleIdTokenValidator,
                       UserService userService,
                       LoginTokenService loginTokenService) {
        this.googleOAuthClient = googleOAuthClient;
        this.googleIdTokenValidator = googleIdTokenValidator;
        this.userService = userService;
        this.loginTokenService = loginTokenService;
    }

    /**
     * authorization code 하나로 구글 로그인을 완료하고 자체 토큰을 발급한다.
     *
     * @param authorizationCode React가 Google에서 받아 전달한 authorization code
     * @return accessToken·refreshToken·isNewUser를 담은 {@link LoginResult}
     */
    public LoginResult loginWithGoogle(String authorizationCode) {
        GoogleTokenResponse tokenResponse = googleOAuthClient.exchangeAuthorizationCode(authorizationCode);
        GoogleUserInfo googleUserInfo = googleIdTokenValidator.validate(tokenResponse.idToken());
        UserFindOrCreateResult userResult = userService.findOrCreate(googleUserInfo);
        return loginTokenService.issueTokens(userResult);
    }
}
