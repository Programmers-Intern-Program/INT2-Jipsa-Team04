package com.jipsa.auth.google;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * application.yaml의 {@code google.oauth.*}에서 바인딩되는 구글 OAuth 설정.
 * 그 값들은 다시 GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REDIRECT_URI 환경변수를
 * 읽는다. 하드코딩된 값은 없다 — 토큰 엔드포인트만 합리적인 기본값을 갖되 GOOGLE_TOKEN_URI로
 * 재정의할 수 있다.
 */
@ConfigurationProperties(prefix = "google.oauth")
public record GoogleOAuthProperties(
        String clientId,
        String clientSecret,
        String redirectUri,
        String tokenUri
) {
    public GoogleOAuthProperties {
        // 엔드포인트 하드 기본값: 환경변수가 누락돼도 토큰 교환이 조용히 엉뚱한
        // 호스트를 향하는 일이 없도록 보장한다.
        if (tokenUri == null || tokenUri.isBlank()) {
            tokenUri = "https://oauth2.googleapis.com/token";
        }
    }
}
