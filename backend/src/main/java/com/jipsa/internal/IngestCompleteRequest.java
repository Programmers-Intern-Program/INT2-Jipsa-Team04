package com.jipsa.internal;

import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.NotNull;

public record IngestCompleteRequest(
        @JsonProperty("success") @NotNull Boolean success,
        @JsonProperty("error_message") String errorMessage
) {
}