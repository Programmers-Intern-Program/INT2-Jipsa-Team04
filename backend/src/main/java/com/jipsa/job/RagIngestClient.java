package com.jipsa.job;

import com.jipsa.internal.IngestManifest;
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
public class RagIngestClient {

    private static final Logger log = LoggerFactory.getLogger(RagIngestClient.class);
    private static final Duration CONNECT_TIMEOUT = Duration.ofSeconds(3);

    private final RestClient restClient;
    private final String baseUrl;
    private final String token;

    public RagIngestClient(@Value("${app.rag.base-url:}") String baseUrl,
                           @Value("${app.rag.token:}") String token,
                           @Value("${app.rag.read-timeout-ms:120000}") long readTimeoutMs) {
        this.baseUrl = baseUrl;
        this.token = token;
        this.restClient = RestClient.builder()
                .requestFactory(requestFactory(Duration.ofMillis(readTimeoutMs)))
                .build();
    }

    public void push(IngestManifest manifest) {
        if (baseUrl == null || baseUrl.isBlank()) {
            throw new IllegalStateException("app.rag.base-url이 설정되지 않아 인제스트를 진행할 수 없습니다.");
        }
        restClient.post()
                .uri(baseUrl + "/ingest")
                .header("X-Internal-Token", token)
                .contentType(MediaType.APPLICATION_JSON)
                .body(manifest)
                .retrieve()
                .toBodilessEntity();
        log.info("Pushed ingest manifest for file {} to RAG", manifest.fileIdx());
    }

    private static ClientHttpRequestFactory requestFactory(Duration readTimeout) {
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(CONNECT_TIMEOUT);
        factory.setReadTimeout(readTimeout);
        return factory;
    }
}