package com.jipsa.auth.google;

import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * authorization code 교환 후 구글 토큰 엔드포인트가 돌려주는 응답 바디.
 *
 * <p>{@code accessToken} / {@code refreshToken}은 메모리 상의 교환 단계에서만 담기며
 * 절대 저장하지 않는다. 신원은 오직 {@code idToken}(서명된 JWT)에서만 확립되며, 이는
 * {@link GoogleIdTokenValidator}가 검증한다.
 */
public record GoogleTokenResponse(
        @JsonProperty("access_token") String accessToken,
        @JsonProperty("id_token") String idToken,
        @JsonProperty("refresh_token") String refreshToken,
        @JsonProperty("expires_in") Long expiresIn,
        @JsonProperty("token_type") String tokenType,
        @JsonProperty("scope") String scope
) {
}
