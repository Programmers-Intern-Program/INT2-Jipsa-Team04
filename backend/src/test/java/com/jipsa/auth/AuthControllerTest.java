package com.jipsa.auth;

import com.jipsa.auth.google.GoogleAuthException;
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
    // JwtService를 요구한다. addFilters = false라 실제로 쓰이진 않지만 컨텍스트를 띄우려면 빈은 필요하다.
    @MockitoBean
    private com.jipsa.auth.JwtService jwtService;

    @Test
    void 정상_로그인시_200과_accessToken_refreshToken_isNewUser를_반환한다() throws Exception {
        given(authService.loginWithGoogle("valid-code"))
                .willReturn(new LoginResult("access-jwt", "refresh-raw", true));

        mockMvc.perform(post("/api/v1/auth/oauth/google")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"authorizationCode\":\"valid-code\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.success").value(true))
                .andExpect(jsonPath("$.data.accessToken").value("access-jwt"))
                .andExpect(jsonPath("$.data.refreshToken").value("refresh-raw"))
                .andExpect(jsonPath("$.data.isNewUser").value(true))
                .andExpect(jsonPath("$.error").value(nullValue()));

        verify(authService).loginWithGoogle("valid-code");
    }

    @Test
    void authorizationCode가_누락되면_400() throws Exception {
        mockMvc.perform(post("/api/v1/auth/oauth/google")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void authorizationCode가_null이면_400() throws Exception {
        mockMvc.perform(post("/api/v1/auth/oauth/google")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"authorizationCode\":null}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void authorizationCode가_blank면_400() throws Exception {
        mockMvc.perform(post("/api/v1/auth/oauth/google")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"authorizationCode\":\"   \"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void GoogleAuthException이면_401() throws Exception {
        given(authService.loginWithGoogle(any()))
                .willThrow(new GoogleAuthException("유효하지 않은 Google id_token입니다."));

        mockMvc.perform(post("/api/v1/auth/oauth/google")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"authorizationCode\":\"bad-code\"}"))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void AccountLoginBlockedException이면_403() throws Exception {
        given(authService.loginWithGoogle(any()))
                .willThrow(new AccountLoginBlockedException("탈퇴 이력이 있는 계정입니다."));

        mockMvc.perform(post("/api/v1/auth/oauth/google")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"authorizationCode\":\"blocked-code\"}"))
                .andExpect(status().isForbidden());
    }
}
