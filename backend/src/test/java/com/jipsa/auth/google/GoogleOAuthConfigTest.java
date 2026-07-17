package com.jipsa.auth.google;

import org.junit.jupiter.api.Test;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

import java.net.ServerSocket;
import java.net.Socket;
import java.net.SocketTimeoutException;
import java.net.URI;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * {@link GoogleOAuthConfig}가 timeout이 적용된 RestClient를 만드는지 검증한다.
 *
 * <p>Spring 내부 구현(요청 팩토리의 private 필드)에 기대는 대신, 응답을 미루는 로컬 소켓
 * 서버로 실제 호출을 보내 read timeout이 관측 가능한 동작으로 발동하는지 확인한다 —
 * 이렇게 하면 프레임워크 내부가 바뀌어도 깨지지 않으면서 "timeout이 실제 호출 경로에
 * 반영된다"는 핵심 계약을 검증할 수 있다. timeout 값·기본값 자체는 레코드의 공개
 * 접근자만으로 확인한다.
 */
class GoogleOAuthConfigTest {

    private final GoogleOAuthConfig config = new GoogleOAuthConfig();

    @Test
    void googleRestClientAppliesConfiguredReadTimeoutToActualCall() throws Exception {
        // 연결은 즉시 받아주되 응답은 보내지 않는 서버 — read 단계에서 timeout이 걸려야 한다.
        try (ServerSocket server = new ServerSocket(0)) {
            int port = server.getLocalPort();
            Thread stalling = new Thread(() -> {
                try (Socket ignored = server.accept()) {
                    Thread.sleep(2000); // 응답을 보내지 않고 read timeout 창을 넘긴다.
                } catch (Exception ignored) {
                    // 테스트가 먼저 끝나 소켓이 닫히는 것은 정상 경로다.
                }
            });
            stalling.setDaemon(true);
            stalling.start();

            String url = "http://127.0.0.1:" + port + "/token";
            // read timeout을 아주 짧게(300ms) 줘서, 발동한 timeout이 기본값이 아니라
            // "설정한 값"이 반영된 결과임을 확인한다. connect는 로컬이라 넉넉히 준다.
            GoogleOAuthProperties properties = new GoogleOAuthProperties(
                    "client-id", "client-secret", "https://app/callback", url, 1000, 300);

            RestClient restClient = config.googleRestClient(properties);

            assertThatThrownBy(() -> restClient.post()
                    .uri(URI.create(url))
                    .body("code=x")
                    .retrieve()
                    .body(String.class))
                    .isInstanceOf(RestClientException.class)
                    .hasRootCauseInstanceOf(SocketTimeoutException.class);
        }
    }

    @Test
    void timeoutPropertiesFallBackToDefaultsWhenUnsetAndHonorExplicitValues() {
        // 설정 누락(0) 시 안전한 기본값(3000ms / 5000ms)이 강제되어야 한다.
        GoogleOAuthProperties unset =
                new GoogleOAuthProperties("client-id", "client-secret", "https://app/callback", null, 0, 0);
        assertThat(unset.connectTimeoutMs()).isEqualTo(3000);
        assertThat(unset.readTimeoutMs()).isEqualTo(5000);

        // 명시한 값은 그대로 반영되어야 한다.
        GoogleOAuthProperties explicit =
                new GoogleOAuthProperties("client-id", "client-secret", "https://app/callback", null, 1500, 2500);
        assertThat(explicit.connectTimeoutMs()).isEqualTo(1500);
        assertThat(explicit.readTimeoutMs()).isEqualTo(2500);
    }
}
