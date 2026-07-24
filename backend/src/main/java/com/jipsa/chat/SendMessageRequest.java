package com.jipsa.chat;

import java.util.List;

public record SendMessageRequest(
        String question,
        List<Long> fileIds,
        Integer topK,
        Double scoreThreshold
) {
}