package com.jipsa.auth.google;

/**
 * A verified Google identity — produced only after {@link GoogleIdTokenValidator}
 * has checked the id_token's signature, issuer, audience, expiry, sub, and email_verified.
 *
 * <p>{@code sub} is Google's stable, unique user identifier and is what we use to
 * link/identify accounts (NOT email, which can change or be reused).
 */
public record GoogleUserInfo(
        String sub,
        String email,
        boolean emailVerified,
        String name,
        String picture
) {
}
