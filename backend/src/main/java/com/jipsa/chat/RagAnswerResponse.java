package com.jipsa.chat;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

@JsonIgnoreProperties(ignoreUnknown = true)
public record RagAnswerResponse(
        @JsonProperty("answer") String answer,
        @JsonProperty("status") String status,
        @JsonProperty("sources") List<RagAnswerSource> sources,
        @JsonProperty("model") String model,
        @JsonProperty("usage") RagAnswerUsage usage,
        @JsonProperty("stop_reason") String stopReason
) {
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record RagAnswerUsage(
            @JsonProperty("input_tokens") Integer inputTokens,
            @JsonProperty("output_tokens") Integer outputTokens
    ) {
    }
}