package com.jipsa.auth.google;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestClient;

import java.net.SocketTimeoutException;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.content;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.method;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.requestTo;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withStatus;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;
import static org.springframework.http.HttpMethod.POST;

class GoogleOAuthClientTest {

    private static final String TOKEN_URI = "https://oauth2.googleapis.com/token";

    private MockRestServiceServer server;
    private GoogleOAuthClient client;

    @BeforeEach
    void setUp() {
        RestClient.Builder builder = RestClient.builder();
        server = MockRestServiceServer.bindTo(builder).build();
        RestClient restClient = builder.build();
        GoogleOAuthProperties properties =
                new GoogleOAuthProperties("client-id", "client-secret", "https://app/callback", TOKEN_URI, 3000, 5000);
        client = new GoogleOAuthClient(restClient, properties);
    }

    @Test
    void sendsAllRequiredParametersAndParsesResponse() {
        server.expect(requestTo(TOKEN_URI))
                .andExpect(method(POST))
                .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_FORM_URLENCODED))
                .andExpect(content().formData(formOf(
                        "code", "auth-code-123",
                        "client_id", "client-id",
                        "client_secret", "client-secret",
                        "redirect_uri", "https://app/callback",
                        "grant_type", "authorization_code")))
                .andRespond(withSuccess(
                        "{\"access_token\":\"ya29.x\",\"id_token\":\"id.jwt.token\","
                                + "\"expires_in\":3599,\"token_type\":\"Bearer\",\"scope\":\"openid email\"}",
                        MediaType.APPLICATION_JSON));

        GoogleTokenResponse response = client.exchangeAuthorizationCode("auth-code-123");

        assertThat(response.idToken()).isEqualTo("id.jwt.token");
        assertThat(response.accessToken()).isEqualTo("ya29.x");
        assertThat(response.tokenType()).isEqualTo("Bearer");
        server.verify();
    }

    @Test
    void throwsAuthFailedWhenGoogleReturns4xx() {
        // 잘못된 authorization code 등 클라이언트 인증 실패 → 401 흐름 유지.
        server.expect(requestTo(TOKEN_URI))
                .andRespond(withStatus(HttpStatus.BAD_REQUEST)
                        .body("{\"error\":\"invalid_grant\"}")
                        .contentType(MediaType.APPLICATION_JSON));

        assertThatThrownBy(() -> client.exchangeAuthorizationCode("bad-code"))
                .isInstanceOf(GoogleAuthException.class)
                .extracting(ex -> ((GoogleAuthException) ex).getStatus())
                .isEqualTo(HttpStatus.UNAUTHORIZED);
        server.verify();
    }

    @Test
    void throwsUnavailableAsBadGatewayWhenGoogleReturns5xx() {
        // 구글 측 장애(5xx)는 인증 실패가 아니라 upstream 오류 → 502.
        server.expect(requestTo(TOKEN_URI))
                .andRespond(withStatus(HttpStatus.INTERNAL_SERVER_ERROR)
                        .body("{\"error\":\"internal_failure\"}")
                        .contentType(MediaType.APPLICATION_JSON));

        assertThatThrownBy(() -> client.exchangeAuthorizationCode("auth-code-123"))
                .isInstanceOf(GoogleAuthUnavailableException.class)
                .extracting(ex -> ((GoogleAuthUnavailableException) ex).getStatus())
                .isEqualTo(HttpStatus.BAD_GATEWAY);
        server.verify();
    }

    @Test
    void throwsUnavailableAsServiceUnavailableOnNetworkFailure() {
        // timeout·DNS·connection·I/O 오류는 ResourceAccessException으로 올라온다 → 503.
        server.expect(requestTo(TOKEN_URI))
                .andRespond(request -> {
                    throw new SocketTimeoutException("Read timed out");
                });

        assertThatThrownBy(() -> client.exchangeAuthorizationCode("auth-code-123"))
                .isInstanceOf(GoogleAuthUnavailableException.class)
                .extracting(ex -> ((GoogleAuthUnavailableException) ex).getStatus())
                .isEqualTo(HttpStatus.SERVICE_UNAVAILABLE);
        server.verify();
    }

    @Test
    void throwsWhenIdTokenMissing() {
        server.expect(requestTo(TOKEN_URI))
                .andRespond(withSuccess("{\"access_token\":\"ya29.x\"}", MediaType.APPLICATION_JSON));

        assertThatThrownBy(() -> client.exchangeAuthorizationCode("auth-code-123"))
                .isInstanceOf(GoogleAuthException.class);
        server.verify();
    }

    private static org.springframework.util.MultiValueMap<String, String> formOf(String... kv) {
        org.springframework.util.LinkedMultiValueMap<String, String> map =
                new org.springframework.util.LinkedMultiValueMap<>();
        for (int i = 0; i < kv.length; i += 2) {
            map.add(kv[i], kv[i + 1]);
        }
        return map;
    }
}
