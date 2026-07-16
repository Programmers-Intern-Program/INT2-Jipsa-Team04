package com.jipsa.auth;

/**
 * 로그인 성공 결과. 자체 발급한 Access/Refresh 토큰과 신규 가입 여부를 함께 전달한다.
 *
 * <p>API 명세의 {@code POST /auth/oauth/google} 응답 {@code {accessToken, refreshToken, isNewUser}}과
 * 1:1로 대응한다. 컨트롤러 연결(응답 매핑)은 다음 단계 소관이다.
 *
 * <p>{@code isNewUser}는 {@link com.jipsa.user.UserFindOrCreateResult#isNewUser()}에서 온 값을
 * 그대로 전달한 것이다 — 토큰 발급 로직은 신규/기존 사용자를 구분하지 않는다.
 */
public record LoginResult(String accessToken, String refreshToken, boolean isNewUser) {
}
