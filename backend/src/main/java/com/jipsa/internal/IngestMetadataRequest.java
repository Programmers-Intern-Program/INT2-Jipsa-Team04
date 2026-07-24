package com.jipsa.internal;

import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.NotNull;

import java.util.List;

public record IngestMetadataRequest(
        @JsonProperty("success") @NotNull Boolean success,
        @JsonProperty("error_message") String errorMessage,
        @JsonProperty("summary") String summary,
        @JsonProperty("keywords") List<String> keywords,
        @JsonProperty("confidence") Double confidence,
        @JsonProperty("entities") Entities entities
) {
    public record Entities(
            @JsonProperty("dates") List<String> dates,
            @JsonProperty("people") List<String> people,
            @JsonProperty("amounts") List<String> amounts,
            @JsonProperty("project") String project
    ) {
    }
}