package com.jipsa.auth;

import jakarta.validation.constraints.NotBlank;

/**
 * {@code POST /api/v1/auth/oauth/google} 요청 바디.
 *
 * <p>React가 Google에서 받아 넘겨주는 authorization code와, 그 code를 이번 로그인 시도에
 * 묶기 위한 PKCE {@code codeVerifier}를 담는다. 프론트는 로그인 시작 시 code_verifier를 만들어
 * SHA-256(S256)으로 해싱한 code_challenge를 Google authorize 요청에 실어 보내고, 콜백에서
 * 원문 code_verifier를 이 바디에 함께 넘긴다 — 백엔드는 이를 Google 토큰 교환에 그대로 전달한다.
 * 두 필드 모두 {@link NotBlank}라 null·빈 문자열·공백만 있는 값은 컨트롤러 진입 전 검증에서 걸러져
 * {@code GlobalExceptionHandler}가 400으로 응답한다.
 */
public record GoogleLoginRequest(
        @NotBlank String authorizationCode,
        @NotBlank String codeVerifier
) {
}
