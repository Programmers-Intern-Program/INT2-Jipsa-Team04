package com.jipsa.auth.google;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.web.client.HttpServerErrorException;
import org.springframework.web.client.ResourceAccessException;
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
     * redirect_uri, grant_type}을 application/x-www-form-urlencoded로 전송하며,
     * PKCE {@code code_verifier}를 함께 실어 code가 이번 로그인 시도에서 발급된 것임을
     * 구글이 code_challenge(S256)와 대조·검증하게 한다.
     *
     * @param authorizationCode 구글에서 발급받은 authorization code
     * @param codeVerifier       PKCE 원문 verifier (authorize 요청의 code_challenge와 짝을 이룸)
     * @return 구글 토큰 엔드포인트 응답({@link GoogleTokenResponse})
     * @throws GoogleAuthException 구글이 4xx(잘못된 code·verifier 불일치 등)를 반환하거나 응답에 id_token이 없을 때 — 401
     * @throws GoogleAuthUnavailableException 구글 5xx·비정상 응답(502)이나 timeout·네트워크 오류(503)로
     *         교환에 실패했을 때 — 클라이언트 인증 정보 문제가 아닌 구글 측/네트워크 장애
     */
    public GoogleTokenResponse exchangeAuthorizationCode(String authorizationCode, String codeVerifier) {
        MultiValueMap<String, String> form = new LinkedMultiValueMap<>();
        form.add("code", authorizationCode);
        form.add("client_id", properties.clientId());
        form.add("client_secret", properties.clientSecret());
        form.add("redirect_uri", properties.redirectUri());
        form.add("grant_type", GRANT_TYPE);
        form.add("code_verifier", codeVerifier);

        GoogleTokenResponse response;
        try {
            response = restClient.post()
                    .uri(properties.tokenUri())
                    .contentType(MediaType.APPLICATION_FORM_URLENCODED)
                    .body(form)
                    .retrieve()
                    .body(GoogleTokenResponse.class);
        } catch (HttpClientErrorException e) {
            // 구글 4xx: 잘못된 authorization code 등 클라이언트 인증 실패 → 401 흐름 유지.
            logTokenExchangeFailure(e);
            throw new GoogleAuthException("Google 토큰 교환에 실패했습니다.");
        } catch (HttpServerErrorException e) {
            // 구글 5xx: 구글 측 장애 → 인증 실패가 아니라 upstream 오류(502).
            logTokenExchangeFailure(e);
            throw GoogleAuthUnavailableException.badGateway("Google 인증 서버 오류로 토큰 교환에 실패했습니다.");
        } catch (ResourceAccessException e) {
            // timeout·DNS 실패·connection refused·I/O 오류: 구글에 닿지 못함 → 503.
            // ResourceAccessException은 원인 예외(SocketTimeoutException 등)를 메시지에 담을 수 있으므로
            // 여기서는 원인을 로깅하지 않고 예외 타입만 남긴다(민감값 노출 방지 + 요청 폼은 어차피 미포함).
            log.warn("Google 토큰 교환 실패 - 네트워크 오류: {}", e.getClass().getSimpleName());
            throw GoogleAuthUnavailableException.serviceUnavailable("Google 인증 서버에 연결하지 못했습니다.");
        } catch (RestClientException e) {
            // 그 외(응답 파싱 실패·비정상 Content-Type 등): 신뢰할 수 없는 upstream 응답 → 502.
            logTokenExchangeFailure(e);
            throw GoogleAuthUnavailableException.badGateway("Google 토큰 응답을 처리하지 못했습니다.");
        }

        if (response == null || response.idToken() == null || response.idToken().isBlank()) {
            throw new GoogleAuthException("Google 토큰 응답에 id_token이 없습니다.");
        }
        return response;
    }

    /**
     * 토큰 교환 실패 원인 디버깅용 로그. 구글이 HTTP 오류 응답(4xx/5xx)을 준 경우에만
     * 상태 코드와 구글 표준 오류 본문의 {@code error}·{@code error_description}만 출력한다.
     * authorizationCode·client_secret·code_verifier·access/refresh/id_token 등 민감값은 절대 출력하지 않는다
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
