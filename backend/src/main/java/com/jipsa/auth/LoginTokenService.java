package com.jipsa.auth;

import com.jipsa.user.UserFindOrCreateResult;
import org.springframework.stereotype.Service;

/**
 * 로그인한 사용자에게 자체 Access/Refresh 토큰을 발급하고 {@link LoginResult}로 조립한다.
 *
 * <p>3단계 산출물인 {@link UserFindOrCreateResult}를 입력으로 받아, 신규/기존 사용자를
 * 구분하지 않고 <b>동일한 경로</b>로 토큰을 발급한다. {@code isNewUser}는 그대로 결과에 전달한다.
 *
 * <p>이 서비스는 아직 어떤 컨트롤러에도 연결하지 않는다(AuthController 연결은 다음 단계).
 */
@Service
public class LoginTokenService {

    private final JwtService jwtService;
    private final RefreshTokenService refreshTokenService;

    public LoginTokenService(JwtService jwtService, RefreshTokenService refreshTokenService) {
        this.jwtService = jwtService;
        this.refreshTokenService = refreshTokenService;
    }

    /**
     * find-or-create 결과의 사용자로 Access/Refresh 토큰을 발급한다.
     *
     * @param result 3단계 find-or-create 결과 (user + isNewUser)
     * @return accessToken(JWT), refreshToken(랜덤 원문), isNewUser
     */
    public LoginResult issueTokens(UserFindOrCreateResult result) {
        Long userId = result.user().getId();
        String role = result.user().getRole();
        String accessToken = jwtService.generateToken(userId, role); // JWT Access 토큰 생성(role 클레임 포함)
        String refreshToken = refreshTokenService.issue(userId);    // Refresh 토큰(원문) 발급·저장
        // isNewUser는 3단계 결과값을 그대로 전달 (토큰 발급은 신규/기존을 구분하지 않음)
        return new LoginResult(accessToken, refreshToken, result.isNewUser());
    }
}
