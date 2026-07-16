package com.jipsa.auth;

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
     * {@code ApiResponse}로 감싸지 않고 그대로 반환한다.
     */
    @PostMapping("/oauth/google")
    public LoginResult googleLogin(@Valid @RequestBody GoogleLoginRequest request) {
        return authService.loginWithGoogle(request.authorizationCode());
    }
}
