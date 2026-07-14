package com.jipsa.auth.google;

import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

/**
 * Talks to Google's token endpoint — and nothing else.
 *
 * <p>Sole responsibility: exchange an authorization code for Google's token response
 * over HTTP. It does NOT verify the id_token (that is {@link GoogleIdTokenValidator}'s
 * job) and does NOT persist any Google token. Keeping the HTTP concern isolated makes
 * the exchange trivially testable with MockRestServiceServer.
 */
@Component
public class GoogleOAuthClient {

    private static final String GRANT_TYPE = "authorization_code";

    private final RestClient restClient;
    private final GoogleOAuthProperties properties;

    public GoogleOAuthClient(RestClient googleRestClient, GoogleOAuthProperties properties) {
        this.restClient = googleRestClient;
        this.properties = properties;
    }

    /**
     * Exchange the authorization code React obtained from Google for a token response.
     * Sends {@code code, client_id, client_secret, redirect_uri, grant_type} as
     * application/x-www-form-urlencoded, per Google's token-endpoint contract.
     *
     * @throws GoogleAuthException if Google returns an error or omits the id_token.
     */
    public GoogleTokenResponse exchangeAuthorizationCode(String authorizationCode) {
        MultiValueMap<String, String> form = new LinkedMultiValueMap<>();
        form.add("code", authorizationCode);
        form.add("client_id", properties.clientId());
        form.add("client_secret", properties.clientSecret());
        form.add("redirect_uri", properties.redirectUri());
        form.add("grant_type", GRANT_TYPE);

        GoogleTokenResponse response;
        try {
            response = restClient.post()
                    .uri(properties.tokenUri())
                    .contentType(MediaType.APPLICATION_FORM_URLENCODED)
                    .body(form)
                    .retrieve()
                    .body(GoogleTokenResponse.class);
        } catch (RestClientException e) {
            // Covers 4xx/5xx from Google as well as transport/parse failures.
            throw new GoogleAuthException("Google 토큰 교환에 실패했습니다.");
        }

        if (response == null || response.idToken() == null || response.idToken().isBlank()) {
            throw new GoogleAuthException("Google 토큰 응답에 id_token이 없습니다.");
        }
        return response;
    }
}
