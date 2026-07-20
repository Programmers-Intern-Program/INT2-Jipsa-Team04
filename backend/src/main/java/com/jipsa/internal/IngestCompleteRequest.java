package com.jipsa.internal;

import com.fasterxml.jackson.annotation.JsonProperty;

public record IngestCompleteRequest(
        @JsonProperty("success") boolean success,
        @JsonProperty("error_message") String errorMessage
) {
}