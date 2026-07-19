package com.jipsa.auth;

import jakarta.validation.constraints.NotBlank;

/**
 * {@code POST /api/v1/auth/logout} 요청 바디.
 *
 * <p>클라이언트가 보관 중인 Refresh Token 원문 하나만 담는다.
 * {@link NotBlank}라 null·빈 문자열·공백만 있는 값은 컨트롤러 진입 전 검증에서 걸러져
 * {@code GlobalExceptionHandler}가 400으로 응답한다.
 *
 * <p>여기 담긴 원문은 로그에 출력하지 않는다 — 서버는 SHA-256 해시로만 조회한다.
 */
public record LogoutRequest(@NotBlank String refreshToken) {
}
