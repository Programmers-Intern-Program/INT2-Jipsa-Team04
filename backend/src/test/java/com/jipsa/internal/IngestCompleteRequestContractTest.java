package com.jipsa.internal;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class IngestCompleteRequestContractTest {

    private final ObjectMapper objectMapper = new ObjectMapper();

    @Test
    void deserializesRagSuccessCallbackPayload() throws Exception {
        String json = """
                {
                  "success": true,
                  "index_version": 2,
                  "chunk_count": 2,
                  "chunks": [
                    {
                      "chunk_id": "8d777f38-65d3-5b30-bc6c-4b8f8f2f8612",
                      "chunk_index": 0,
                      "content": "첫 번째 청크",
                      "content_hash": "aaaa",
                      "token_count": 128,
                      "source_metadata": {"page_number": 1}
                    },
                    {
                      "chunk_id": "1b9d6bcd-bbfd-5b1f-9c4e-1d2c3f4a5b6c",
                      "chunk_index": 1,
                      "content": "두 번째 청크",
                      "content_hash": "bbbb",
                      "token_count": null,
                      "source_metadata": {}
                    }
                  ]
                }
                """;

        IngestCompleteRequest request = objectMapper.readValue(json, IngestCompleteRequest.class);

        assertThat(request.success()).isTrue();
        assertThat(request.indexVersion()).isEqualTo(2);
        assertThat(request.chunkCount()).isEqualTo(2);
        assertThat(request.chunks()).hasSize(2);

        IngestCompleteRequest.ChunkPayload first = request.chunks().get(0);
        assertThat(first.chunkId()).isEqualTo("8d777f38-65d3-5b30-bc6c-4b8f8f2f8612");
        assertThat(first.chunkIndex()).isZero();
        assertThat(first.content()).isEqualTo("첫 번째 청크");
        assertThat(first.sourceMetadata()).containsEntry("page_number", 1);
        assertThat(request.chunks().get(1).tokenCount()).isNull();
    }

    @Test
    void deserializesFailureCallbackWithoutChunks() throws Exception {
        String json = """
                {
                  "success": false,
                  "error_message": "parsing failed"
                }
                """;

        IngestCompleteRequest request = objectMapper.readValue(json, IngestCompleteRequest.class);

        assertThat(request.success()).isFalse();
        assertThat(request.errorMessage()).isEqualTo("parsing failed");
        assertThat(request.chunks()).isNull();
    }
}