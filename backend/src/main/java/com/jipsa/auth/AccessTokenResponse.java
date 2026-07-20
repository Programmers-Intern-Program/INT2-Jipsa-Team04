package com.jipsa.auth;

/**
 * {@code POST /api/v1/auth/refresh} 성공 응답 data.
 *
 * <p>새로 발급한 Access Token 하나만 담는다. API 명세의
 * {@code {accessToken}} 응답과 1:1로 대응하며, 이 단계에서는 새 Refresh Token을
 * 발급하지 않으므로 Refresh Token은 포함하지 않는다.
 */
public record AccessTokenResponse(String accessToken) {
}
