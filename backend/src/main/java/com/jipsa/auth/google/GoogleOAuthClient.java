package com.jipsa.auth.google;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestClientResponseException;

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

    private static final Logger log = LoggerFactory.getLogger(GoogleOAuthClient.class);
    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();

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
            logTokenExchangeFailure(e);
            throw new GoogleAuthException("Google 토큰 교환에 실패했습니다.");
        }

        if (response == null || response.idToken() == null || response.idToken().isBlank()) {
            throw new GoogleAuthException("Google 토큰 응답에 id_token이 없습니다.");
        }
        return response;
    }

    /**
     * 토큰 교환 실패 원인 디버깅용 로그. 구글이 HTTP 오류 응답(4xx/5xx)을 준 경우에만
     * 상태 코드와 구글 표준 오류 본문의 {@code error}·{@code error_description}만 출력한다.
     * authorizationCode·client_secret·access/refresh/id_token 등 민감값은 절대 출력하지 않는다
     * (그 값들은 요청 폼·성공 응답에만 존재하며, 여기서 읽는 오류 본문에는 담기지 않는다).
     */
    private void logTokenExchangeFailure(RestClientException e) {
        if (!(e instanceof RestClientResponseException rcre)) {
            return;
        }
        String error = null;
        String errorDescription = null;
        try {
            JsonNode body = OBJECT_MAPPER.readTree(rcre.getResponseBodyAsString());
            error = body.path("error").asText(null);
            errorDescription = body.path("error_description").asText(null);
        } catch (Exception parseFailure) {
            // 본문이 JSON이 아니거나 파싱에 실패하면 error 필드는 남기지 않는다(본문 원문은 출력하지 않음).
        }
        log.warn("Google 토큰 교환 실패 - status={}, error={}, error_description={}",
                rcre.getStatusCode(), error, errorDescription);
    }
}
