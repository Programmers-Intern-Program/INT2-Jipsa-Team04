package com.jipsa.search;

public record SearchRequest(
        String query,
        Long folderId
) {
}
