package com.jipsa.auth;

import com.jipsa.auth.google.GoogleAuthException;
import com.jipsa.common.exception.UnauthorizedException;
import com.jipsa.user.AccountLoginBlockedException;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import static org.hamcrest.Matchers.nullValue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * AuthController 웹 레이어 슬라이스 테스트. AuthService는 mock으로 대체하고
 * 요청 검증(@NotBlank → 400), 성공 응답 JSON(LoginResult 그대로), 그리고
 * 서비스가 던진 예외 → HTTP 상태(GlobalExceptionHandler) 매핑만 검증한다.
 *
 * 필터는 addFilters = false로 꺼둔다 — 이 엔드포인트는 SecurityConfig에서 permitAll이고,
 * 슬라이스 테스트에서 검증하려는 건 컨트롤러 동작이지 시큐리티 배선이 아니기 때문이다.
 */
@WebMvcTest(AuthController.class)
@AutoConfigureMockMvc(addFilters = false)
@Import(com.jipsa.common.GlobalExceptionHandler.class)
class AuthControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private AuthService authService;

    // @WebMvcTest 슬라이스에 Filter 타입 빈(JwtAuthenticationFilter)이 포함되고 그 생성자가
    // JwtService/UserRoleCache/UsersRepository를 요구한다. addFilters = false라 실제로 쓰이진
    // 않지만 컨텍스트를 띄우려면 빈은 필요하다.
    @MockitoBean
    private com.jipsa.auth.JwtService jwtService;

    @MockitoBean
    private UserRoleCache userRoleCache;

    @MockitoBean
    private com.jipsa.user.UsersRepository usersRepository;

    @Test
    void 정상_로그인시_200과_accessToken_refreshToken_isNewUser를_반환한다() throws Exception {
        given(authService.loginWithGoogle("valid-code", "verifier-abc"))
                .willReturn(new LoginResult("access-jwt", "refresh-raw", true));

        mockMvc.perform(post("/api/v1/auth/oauth/google")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"authorizationCode\":\"valid-code\",\"codeVerifier\":\"verifier-abc\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.success").value(true))
                .andExpect(jsonPath("$.data.accessToken").value("access-jwt"))
                .andExpect(jsonPath("$.data.refreshToken").value("refresh-raw"))
                .andExpect(jsonPath("$.data.isNewUser").value(true))
                .andExpect(jsonPath("$.error").value(nullValue()));

        verify(authService).loginWithGoogle("valid-code", "verifier-abc");
    }

    @Test
    void authorizationCode가_누락되면_400() throws Exception {
        mockMvc.perform(post("/api/v1/auth/oauth/google")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"codeVerifier\":\"verifier-abc\"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void authorizationCode가_null이면_400() throws Exception {
        mockMvc.perform(post("/api/v1/auth/oauth/google")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"authorizationCode\":null,\"codeVerifier\":\"verifier-abc\"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void authorizationCode가_blank면_400() throws Exception {
        mockMvc.perform(post("/api/v1/auth/oauth/google")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"authorizationCode\":\"   \",\"codeVerifier\":\"verifier-abc\"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void codeVerifier가_누락되면_400() throws Exception {
        mockMvc.perform(post("/api/v1/auth/oauth/google")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"authorizationCode\":\"valid-code\"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void codeVerifier가_null이면_400() throws Exception {
        mockMvc.perform(post("/api/v1/auth/oauth/google")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"authorizationCode\":\"valid-code\",\"codeVerifier\":null}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void codeVerifier가_blank면_400() throws Exception {
        mockMvc.perform(post("/api/v1/auth/oauth/google")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"authorizationCode\":\"valid-code\",\"codeVerifier\":\"   \"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void GoogleAuthException이면_401() throws Exception {
        given(authService.loginWithGoogle(any(), any()))
                .willThrow(new GoogleAuthException("유효하지 않은 Google id_token입니다."));

        mockMvc.perform(post("/api/v1/auth/oauth/google")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"authorizationCode\":\"bad-code\",\"codeVerifier\":\"verifier-abc\"}"))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void AccountLoginBlockedException이면_403() throws Exception {
        given(authService.loginWithGoogle(any(), any()))
                .willThrow(new AccountLoginBlockedException("탈퇴 이력이 있는 계정입니다."));

        mockMvc.perform(post("/api/v1/auth/oauth/google")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"authorizationCode\":\"blocked-code\",\"codeVerifier\":\"verifier-abc\"}"))
                .andExpect(status().isForbidden());
    }

    // --- POST /api/v1/auth/refresh ---

    @Test
    void 정상_재발급시_200과_accessToken을_ApiResponse로_반환한다() throws Exception {
        given(authService.refreshAccessToken("refresh-raw"))
                .willReturn(new AccessTokenResponse("new-access-jwt"));

        mockMvc.perform(post("/api/v1/auth/refresh")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"refreshToken\":\"refresh-raw\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.success").value(true))
                .andExpect(jsonPath("$.data.accessToken").value("new-access-jwt"))
                .andExpect(jsonPath("$.error").value(nullValue()));

        verify(authService).refreshAccessToken("refresh-raw");
    }

    @Test
    void refreshToken이_누락되면_400() throws Exception {
        mockMvc.perform(post("/api/v1/auth/refresh")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void refreshToken이_null이면_400() throws Exception {
        mockMvc.perform(post("/api/v1/auth/refresh")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"refreshToken\":null}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void refreshToken이_blank면_400() throws Exception {
        mockMvc.perform(post("/api/v1/auth/refresh")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"refreshToken\":\"   \"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void 잘못된_토큰이면_UnauthorizedException으로_401() throws Exception {
        given(authService.refreshAccessToken(any()))
                .willThrow(new UnauthorizedException("유효하지 않은 리프레시 토큰입니다."));

        mockMvc.perform(post("/api/v1/auth/refresh")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"refreshToken\":\"bad-token\"}"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.success").value(false))
                .andExpect(jsonPath("$.error.code").value("UNAUTHORIZED"));
    }

    @Test
    void 로그인_불가_계정이면_AccountLoginBlockedException으로_403() throws Exception {
        given(authService.refreshAccessToken(any()))
                .willThrow(new AccountLoginBlockedException("로그인할 수 없는 계정 상태입니다."));

        mockMvc.perform(post("/api/v1/auth/refresh")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"refreshToken\":\"blocked-user-token\"}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error.code").value("ACCOUNT_LOGIN_BLOCKED"));
    }

    // --- POST /api/v1/auth/logout ---

    @Test
    void 정상_로그아웃시_200과_success_true를_ApiResponse로_반환한다() throws Exception {
        mockMvc.perform(post("/api/v1/auth/logout")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"refreshToken\":\"refresh-raw\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.success").value(true))
                .andExpect(jsonPath("$.data.success").value(true))
                .andExpect(jsonPath("$.error").value(nullValue()));

        verify(authService).logout("refresh-raw");
    }

    @Test
    void 로그아웃_refreshToken이_누락되면_400() throws Exception {
        mockMvc.perform(post("/api/v1/auth/logout")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void 로그아웃_refreshToken이_null이면_400() throws Exception {
        mockMvc.perform(post("/api/v1/auth/logout")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"refreshToken\":null}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void 로그아웃_refreshToken이_blank면_400() throws Exception {
        mockMvc.perform(post("/api/v1/auth/logout")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"refreshToken\":\"   \"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void 미존재_또는_위조_토큰이면_UnauthorizedException으로_401() throws Exception {
        org.mockito.BDDMockito.willThrow(new UnauthorizedException("유효하지 않은 리프레시 토큰입니다."))
                .given(authService).logout(any());

        mockMvc.perform(post("/api/v1/auth/logout")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"refreshToken\":\"forged-token\"}"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.success").value(false))
                .andExpect(jsonPath("$.error.code").value("UNAUTHORIZED"));
    }
}
