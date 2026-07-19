package com.jipsa.auth.google;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * application.yaml의 {@code google.oauth.*}에서 바인딩되는 구글 OAuth 설정.
 * 그 값들은 다시 GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REDIRECT_URI 환경변수를
 * 읽는다. 하드코딩된 값은 없다 — 토큰 엔드포인트만 합리적인 기본값을 갖되 GOOGLE_TOKEN_URI로
 * 재정의할 수 있다.
 *
 * <p>{@code connectTimeoutMs} / {@code readTimeoutMs}는 구글 토큰 엔드포인트 호출에 적용할
 * 애플리케이션 레벨 타임아웃(밀리초)이다. GOOGLE_OAUTH_CONNECT_TIMEOUT_MS /
 * GOOGLE_OAUTH_READ_TIMEOUT_MS로 재정의할 수 있으며, 값이 없거나 0 이하면 기본값을 쓴다.
 */
@ConfigurationProperties(prefix = "google.oauth")
public record GoogleOAuthProperties(
        String clientId,
        String clientSecret,
        String redirectUri,
        String tokenUri,
        int connectTimeoutMs,
        int readTimeoutMs
) {
    private static final int DEFAULT_CONNECT_TIMEOUT_MS = 3000;
    private static final int DEFAULT_READ_TIMEOUT_MS = 5000;

    public GoogleOAuthProperties {
        // 엔드포인트 하드 기본값: 환경변수가 누락돼도 토큰 교환이 조용히 엉뚱한
        // 호스트를 향하는 일이 없도록 보장한다.
        if (tokenUri == null || tokenUri.isBlank()) {
            tokenUri = "https://oauth2.googleapis.com/token";
        }
        // 타임아웃 하드 기본값: 설정이 누락(0)되거나 비정상(음수)이면 무한 대기 대신
        // 안전한 기본값을 강제해, 응답 없는 구글/네트워크가 로그인 스레드를 오래 점유하지 못하게 한다.
        if (connectTimeoutMs <= 0) {
            connectTimeoutMs = DEFAULT_CONNECT_TIMEOUT_MS;
        }
        if (readTimeoutMs <= 0) {
            readTimeoutMs = DEFAULT_READ_TIMEOUT_MS;
        }
    }
}
