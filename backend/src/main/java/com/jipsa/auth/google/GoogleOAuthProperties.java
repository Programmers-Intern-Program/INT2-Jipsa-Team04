package com.jipsa.auth.google;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Google OAuth settings, bound from {@code google.oauth.*} in application.yaml,
 * which in turn reads the GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REDIRECT_URI
 * env vars. Nothing here is hardcoded — the token endpoint carries a sensible default
 * but stays overridable via GOOGLE_TOKEN_URI.
 */
@ConfigurationProperties(prefix = "google.oauth")
public record GoogleOAuthProperties(
        String clientId,
        String clientSecret,
        String redirectUri,
        String tokenUri
) {
    public GoogleOAuthProperties {
        // Hard default for the endpoint so a missing env var can never silently
        // point token exchange at the wrong host.
        if (tokenUri == null || tokenUri.isBlank()) {
            tokenUri = "https://oauth2.googleapis.com/token";
        }
    }
}
