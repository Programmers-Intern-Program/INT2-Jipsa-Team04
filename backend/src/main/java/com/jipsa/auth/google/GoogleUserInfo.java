package com.jipsa.auth.google;

/**
 * 검증된 구글 신원 정보 — {@link GoogleIdTokenValidator}가 id_token의 서명, 발급자,
 * audience, 만료, sub, email_verified를 모두 확인한 뒤에만 만들어진다.
 *
 * <p>{@code sub}는 구글의 안정적이고 고유한 사용자 식별자로, 계정을 연결·식별하는 데
 * 사용한다(바뀌거나 재사용될 수 있는 email이 아니다).
 */
public record GoogleUserInfo(
        String sub,
        String email,
        boolean emailVerified,
        String name,
        String picture
) {
}
