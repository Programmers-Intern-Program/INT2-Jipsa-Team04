package com.jipsa.chat;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.http.client.ClientHttpRequestFactory;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

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
        RagAnswerResponse response = restClient.post()
                .uri(baseUrl + "/answer")
                .header("X-Internal-Token", token)
                .contentType(MediaType.APPLICATION_JSON)
                .body(request)
                .retrieve()
                .body(RagAnswerResponse.class);
        log.info("RAG answer for user {} → status {}",
                request.userIdx(), response == null ? "null" : response.status());
        return response;
    }

    private static ClientHttpRequestFactory requestFactory(Duration readTimeout) {
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(CONNECT_TIMEOUT);
        factory.setReadTimeout(readTimeout);
        return factory;
    }
}