package com.jipsa.auth;

import jakarta.validation.constraints.NotBlank;

/**
 * {@code POST /api/v1/auth/oauth/google} 요청 바디.
 *
 * <p>React가 Google에서 받아 넘겨주는 authorization code 하나만 담는다.
 * {@link NotBlank}라 null·빈 문자열·공백만 있는 값은 컨트롤러 진입 전 검증에서 걸러져
 * {@code GlobalExceptionHandler}가 400으로 응답한다.
 */
public record GoogleLoginRequest(@NotBlank String authorizationCode) {
}
