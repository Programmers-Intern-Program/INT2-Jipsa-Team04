package com.jipsa.search;

import java.util.List;

public record SearchResponse(
        List<Item> items
) {
    public record Item(
            Long fileId,
            String name,
            String matchedChunk,
            Double score
    ) {
    }
}
