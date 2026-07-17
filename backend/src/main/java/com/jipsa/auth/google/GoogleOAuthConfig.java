package com.jipsa.auth.google;

import com.google.api.client.googleapis.auth.oauth2.GoogleIdTokenVerifier;
import com.google.api.client.googleapis.javanet.GoogleNetHttpTransport;
import com.google.api.client.json.gson.GsonFactory;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.ClientHttpRequestFactory;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.web.client.RestClient;

import java.security.GeneralSecurityException;
import java.io.IOException;
import java.time.Duration;
import java.util.Collections;

/**
 * 백엔드 authorization-code 방식에 필요한 구글 OAuth 협력 객체들을 등록(wiring)한다.
 * 의도적으로 spring-security의 oauth2Login()은 건드리지 않는다 — 토큰 교환과 id_token
 * 검증은 우리 코드({@link GoogleOAuthClient} / {@link GoogleIdTokenValidator})가 수행한다.
 */
@Configuration
@EnableConfigurationProperties(GoogleOAuthProperties.class)
public class GoogleOAuthConfig {

    /**
     * authorization code를 구글 토큰 엔드포인트로 POST할 때 쓰는 RestClient.
     * {@code RestClient.create()} 대신 {@code RestClient.builder()}로 만들어,
     * connect/read 타임아웃이 적용된 {@link ClientHttpRequestFactory}를 주입한다 —
     * 응답 없는 구글/네트워크가 로그인 요청 스레드를 무한정 점유하지 못하게 하기 위함이다.
     * 이 프로젝트의 webmvc 스타터는 공유 {@code RestClient.Builder} 빈을 자동 구성하지 않으므로
     * 여기서 직접 빌더를 생성한다.
     */
    @Bean
    public RestClient googleRestClient(GoogleOAuthProperties properties) {
        return RestClient.builder()
                .requestFactory(googleTokenRequestFactory(properties))
                .build();
    }

    /**
     * 구글 토큰 엔드포인트 호출용 요청 팩토리. connect/read 타임아웃을 명시해
     * 애플리케이션 레벨에서 대기 시간을 제한한다. 타임아웃 값은 {@code google.oauth.connect-timeout-ms}
     * / {@code google.oauth.read-timeout-ms}로 외부에서 조정할 수 있다(기본값 3000ms / 5000ms).
     */
    ClientHttpRequestFactory googleTokenRequestFactory(GoogleOAuthProperties properties) {
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(Duration.ofMillis(properties.connectTimeoutMs()));
        factory.setReadTimeout(Duration.ofMillis(properties.readTimeoutMs()));
        return factory;
    }

    /**
     * 구글 공식 id_token 검증기. 구글 로그인 문서가 안내하는 방식 그대로 만든다:
     * 신뢰된 java.net 트랜스포트 + 기본 Gson JSON 팩토리. 구글의 공개 서명 키(JWKS, 캐싱됨)를
     * 가져와 id_token의 서명, 발급자(accounts.google.com), 만료를 검사한다. audience는 우리
     * client id로 고정해, 다른 앱용으로 발급된 토큰은 거부한다.
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
