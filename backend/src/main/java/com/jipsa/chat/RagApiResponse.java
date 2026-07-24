package com.jipsa.chat;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

@JsonIgnoreProperties(ignoreUnknown = true)
public record RagApiResponse<T>(
        @JsonProperty("success") Boolean success,
        @JsonProperty("code") String code,
        @JsonProperty("message") String message,
        @JsonProperty("data") T data
) {
}