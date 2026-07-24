package com.jipsa.internal;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class IngestMetadataRequestContractTest {

    private final ObjectMapper objectMapper = new ObjectMapper();

    @Test
    void deserializesRagMetadataPayload() throws Exception {
        String json = """
                {
                  "success": true,
                  "index_version": 3,
                  "summary": "문서 요약",
                  "keywords": ["계약", "임대"],
                  "confidence": 0.91,
                  "entities": {
                    "dates": ["2026-07-24"],
                    "people": ["홍길동"],
                    "amounts": ["1,000,000원"],
                    "project": "임대차 계약"
                  }
                }
                """;

        IngestMetadataRequest request = objectMapper.readValue(json, IngestMetadataRequest.class);

        assertThat(request.success()).isTrue();
        assertThat(request.indexVersion()).isEqualTo(3);
        assertThat(request.summary()).isEqualTo("문서 요약");
        assertThat(request.keywords()).containsExactly("계약", "임대");
        assertThat(request.confidence()).isEqualTo(0.91);
        assertThat(request.entities().people()).containsExactly("홍길동");
        assertThat(request.entities().project()).isEqualTo("임대차 계약");
    }
}