package com.jipsa.auth;

/** JWT에서 검증·추출한 인증 정보. role은 발급 시점 값을 그대로 담고 있다(재검증 없음). */
public record JwtPrincipal(Long userId, String role) {
}
