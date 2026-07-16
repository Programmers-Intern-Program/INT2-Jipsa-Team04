package com.jipsa.auth.google;

import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

/**
 * 구글 토큰 엔드포인트와의 통신만 담당한다 — 그 외 책임은 없다.
 *
 * <p>유일한 역할: authorization code를 HTTP로 구글에 보내 토큰 응답으로 교환한다.
 * id_token 검증({@link GoogleIdTokenValidator}의 역할)은 하지 않으며, 구글 토큰을
 * 저장하지도 않는다. HTTP 관심사만 이 클래스에 격리해 두면 MockRestServiceServer로
 * 교환 로직을 손쉽게 테스트할 수 있다.
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
     * React가 구글에서 받아온 authorization code를 토큰 응답으로 교환한다.
     * 구글 토큰 엔드포인트 규약에 따라 {@code code, client_id, client_secret,
     * redirect_uri, grant_type}을 application/x-www-form-urlencoded로 전송한다.
     *
     * @param authorizationCode 구글에서 발급받은 authorization code
     * @return 구글 토큰 엔드포인트 응답({@link GoogleTokenResponse})
     * @throws GoogleAuthException 구글이 오류를 반환하거나 응답에 id_token이 없을 때
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
            // 구글의 4xx/5xx 응답은 물론 전송/파싱 실패까지 모두 여기서 처리한다.
            throw new GoogleAuthException("Google 토큰 교환에 실패했습니다.");
        }

        if (response == null || response.idToken() == null || response.idToken().isBlank()) {
            throw new GoogleAuthException("Google 토큰 응답에 id_token이 없습니다.");
        }
        return response;
    }
}
