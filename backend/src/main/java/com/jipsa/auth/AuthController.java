package com.jipsa.auth;

import com.jipsa.common.ApiResponse;
import com.jipsa.common.SuccessResponse;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 인증 관련 엔드포인트. 현재는 구글 로그인만 노출한다.
 *
 * <p>컨트롤러 책임은 <b>요청 수신 · 검증 · 위임 · 반환</b>뿐이다. 실제 흐름 조립은
 * {@link AuthService}가 담당하고, 예외 → HTTP 상태 매핑은 {@code GlobalExceptionHandler}가 담당한다.
 */
@RestController
@RequestMapping("/api/v1/auth")
public class AuthController {

    private final AuthService authService;

    public AuthController(AuthService authService) {
        this.authService = authService;
    }

    /**
     * POST /api/v1/auth/oauth/google — 구글 로그인(최초 시 계정 자동 생성).
     *
     * <p>성공 시 HTTP 200과 함께 {@link LoginResult}({@code accessToken, refreshToken, isNewUser})를
     * 공통 응답 규칙에 따라 {@link ApiResponse}로 감싸 반환한다 — 실패 응답(GlobalExceptionHandler)과
     * 동일하게 {@code {success, data, error}} 구조를 갖는다.
     */
    @PostMapping("/oauth/google")
    public ApiResponse<LoginResult> googleLogin(@Valid @RequestBody GoogleLoginRequest request) {
        LoginResult loginResult = authService.loginWithGoogle(request.authorizationCode());
        return ApiResponse.ok(loginResult);
    }

    /**
     * POST /api/v1/auth/refresh — Refresh Token으로 새 Access Token 재발급.
     *
     * <p>요청 {@code {refreshToken}}을 검증하고 유효하면 HTTP 200과 함께
     * {@link AccessTokenResponse}({@code {accessToken}})를 {@link ApiResponse}로 감싸 반환한다.
     * 이 단계에서는 새 Refresh Token을 발급하지 않는다(rotation 없음). 잘못된/만료/폐기 토큰은
     * 401, 로그인 불가 계정은 403으로 {@code GlobalExceptionHandler}가 응답한다.
     */
    @PostMapping("/refresh")
    public ApiResponse<AccessTokenResponse> refresh(@Valid @RequestBody RefreshRequest request) {
        AccessTokenResponse response = authService.refreshAccessToken(request.refreshToken());
        return ApiResponse.ok(response);
    }

    /**
     * POST /api/v1/auth/logout — Refresh Token 폐기(로그아웃).
     *
     * <p>요청 {@code {refreshToken}}을 SHA-256 해시로 조회해 하이브리드 멱등 정책으로 폐기한다:
     * 정상·만료 토큰은 {@code Revoked_At}/{@code Revoked_Reason="LOGOUT"}을 기록하고, 이미 폐기된
     * 토큰은 기존 값을 보존한 채 성공 처리한다. 성공 시 HTTP 200과 함께
     * {@link SuccessResponse}({@code {success:true}})를 {@link ApiResponse}로 감싸 반환한다.
     * 존재하지 않거나 위조된 토큰은 401로 {@code GlobalExceptionHandler}가 응답한다.
     */
    @PostMapping("/logout")
    public ApiResponse<SuccessResponse> logout(@Valid @RequestBody LogoutRequest request) {
        authService.logout(request.refreshToken());
        return ApiResponse.ok(new SuccessResponse(true));
    }
}
