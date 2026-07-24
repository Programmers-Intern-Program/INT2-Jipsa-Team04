package com.jipsa.chat;

import com.jipsa.common.exception.RagUnavailableException;
import com.jipsa.common.exception.RagUpstreamException;
import com.jipsa.common.exception.TooManyRequestsException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.MediaType;
import org.springframework.http.client.ClientHttpRequestFactory;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientResponseException;

import java.time.Duration;

@Component
public class RagAnswerClient {

    private static final Logger log = LoggerFactory.getLogger(RagAnswerClient.class);
    private static final Duration CONNECT_TIMEOUT = Duration.ofSeconds(3);

    private final RestClient restClient;
    private final String baseUrl;
    private final String token;

    public RagAnswerClient(@Value("${app.rag.base-url:}") String baseUrl,
                           @Value("${app.rag.token:}") String token,
                           @Value("${app.rag.answer-timeout-ms:60000}") long readTimeoutMs) {
        this.baseUrl = baseUrl;
        this.token = token;
        this.restClient = RestClient.builder()
                .requestFactory(requestFactory(Duration.ofMillis(readTimeoutMs)))
                .build();
    }

    public RagAnswerResponse answer(RagAnswerRequest request) {
        if (baseUrl == null || baseUrl.isBlank()) {
            throw new IllegalStateException("app.rag.base-url이 설정되지 않아 답변 생성을 진행할 수 없습니다.");
        }
        RagApiResponse<RagAnswerResponse> envelope;
        try {
            envelope = restClient.post()
                    .uri(baseUrl + "/api/v1/rag/answers")
                    .header("X-Internal-Token", token)
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(request)
                    .retrieve()
                    .body(new ParameterizedTypeReference<RagApiResponse<RagAnswerResponse>>() {});
        } catch (RestClientResponseException e) {
            throw translate(e);
        } catch (ResourceAccessException e) {
            log.warn("RAG 답변 연결 실패: {}", e.getMessage());
            throw new RagUnavailableException("RAG 서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.");
        }
        if (envelope == null || envelope.data() == null) {
            throw new RagUpstreamException("RAG 답변 응답이 비어 있습니다.");
        }
        RagAnswerResponse data = envelope.data();
        log.info("RAG answer for user {} → status {}", request.userIdx(), data.status());
        return data;
    }

    private RuntimeException translate(RestClientResponseException e) {
        int status = e.getStatusCode().value();
        log.warn("RAG 답변 오류 상태 {}: {}", status, e.getMessage());
        if (status == 429) {
            return new TooManyRequestsException("RAG 요청이 일시적으로 제한되었습니다. 잠시 후 다시 시도해 주세요.");
        }
        if (e.getStatusCode().is5xxServerError()) {
            return new RagUnavailableException("RAG 서버가 일시적으로 응답하지 못합니다. 잠시 후 다시 시도해 주세요.");
        }
        return new RagUpstreamException("RAG 답변 요청이 거부되었습니다(" + status + ").");
    }

    private static ClientHttpRequestFactory requestFactory(Duration readTimeout) {
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(CONNECT_TIMEOUT);
        factory.setReadTimeout(readTimeout);
        return factory;
    }
}