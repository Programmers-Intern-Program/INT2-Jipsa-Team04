package com.jipsa.metadata;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

import java.util.List;

@JsonIgnoreProperties(ignoreUnknown = true)
public record AutoMetadataResult(
        String summary,
        List<String> keywords,
        Entities entities,
        String documentType,
        Double confidence
) {
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Entities(
            List<String> dates,
            List<String> people,
            List<String> amounts,
            String project
    ) {
    }
}