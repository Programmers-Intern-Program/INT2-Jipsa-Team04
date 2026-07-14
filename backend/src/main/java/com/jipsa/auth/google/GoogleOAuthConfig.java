package com.jipsa.auth.google;

import com.google.api.client.googleapis.auth.oauth2.GoogleIdTokenVerifier;
import com.google.api.client.googleapis.javanet.GoogleNetHttpTransport;
import com.google.api.client.json.gson.GsonFactory;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.client.RestClient;

import java.security.GeneralSecurityException;
import java.io.IOException;
import java.util.Collections;

/**
 * Wires the Google OAuth collaborators for the backend-side authorization-code flow.
 * Deliberately does NOT touch spring-security's oauth2Login() — token exchange and
 * id_token verification are performed by our own code ({@link GoogleOAuthClient} /
 * {@link GoogleIdTokenValidator}).
 */
@Configuration
@EnableConfigurationProperties(GoogleOAuthProperties.class)
public class GoogleOAuthConfig {

    /**
     * Plain RestClient used to POST the authorization code to Google's token endpoint.
     * Built directly via {@code RestClient.create()} rather than an injected
     * {@code RestClient.Builder} — this project's webmvc starter does not auto-configure
     * that builder bean, and this collaborator needs no shared customizations.
     */
    @Bean
    public RestClient googleRestClient() {
        return RestClient.create();
    }

    /**
     * Google's official id_token verifier, built the way the Google sign-in docs show:
     * a trusted java.net transport plus the default Gson JSON factory. It fetches
     * Google's public signing keys (JWKS, cached) and checks the id_token's signature,
     * issuer (accounts.google.com) and expiry. The audience is pinned to our client id
     * so tokens minted for other apps are rejected.
     */
    @Bean
    public GoogleIdTokenVerifier googleIdTokenVerifier(GoogleOAuthProperties properties)
            throws GeneralSecurityException, IOException {
        return new GoogleIdTokenVerifier.Builder(
                GoogleNetHttpTransport.newTrustedTransport(),
                GsonFactory.getDefaultInstance())
                .setAudience(Collections.singletonList(properties.clientId()))
                .build();
    }
}
