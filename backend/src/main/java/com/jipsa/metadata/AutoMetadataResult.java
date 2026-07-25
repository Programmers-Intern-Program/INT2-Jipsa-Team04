package com.jipsa.metadata;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

import java.util.List;

@JsonIgnoreProperties(ignoreUnknown = true)
public record AutoMetadataResult(
        String summary,
        List<String> keywords,
        List<String> entities,
        String documentType,
        Double confidence
) {
}