package com.jipsa.auth.google;

import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * Response body from Google's token endpoint after exchanging an authorization code.
 *
 * <p>{@code accessToken} / {@code refreshToken} are captured only for the in-memory
 * exchange step — they are NEVER persisted. Identity is established solely from
 * {@code idToken} (a signed JWT), which {@link GoogleIdTokenValidator} verifies.
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
