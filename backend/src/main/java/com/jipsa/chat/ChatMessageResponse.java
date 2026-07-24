package com.jipsa.chat;

import java.util.List;

public record ChatMessageResponse(
        Long messageId,
        String answer,
        String status,
        List<Citation> citations
) {
    public record Citation(
            Long fileId,
            String fileName,
            Integer page,
            String sectionTitle,
            String excerpt,
            Double score
    ) {
    }
}