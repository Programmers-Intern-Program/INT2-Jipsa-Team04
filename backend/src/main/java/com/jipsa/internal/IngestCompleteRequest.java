package com.jipsa.internal;

import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.NotNull;

import java.util.List;
import java.util.Map;

public record IngestCompleteRequest(
        @JsonProperty("success") @NotNull Boolean success,
        @JsonProperty("error_message") String errorMessage,
        @JsonProperty("index_version") Integer indexVersion,
        @JsonProperty("chunk_count") Integer chunkCount,
        @JsonProperty("chunks") List<ChunkPayload> chunks
) {
    public record ChunkPayload(
            @JsonProperty("chunk_id") String chunkId,
            @JsonProperty("chunk_index") Integer chunkIndex,
            @JsonProperty("content") String content,
            @JsonProperty("content_hash") String contentHash,
            @JsonProperty("token_count") Integer tokenCount,
            @JsonProperty("source_metadata") Map<String, Object> sourceMetadata
    ) {
    }
}