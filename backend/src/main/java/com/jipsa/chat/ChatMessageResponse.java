package com.jipsa.chat;

import java.time.LocalDateTime;
import java.util.List;

public record ChatMessageResponse(
        Long messageId,
        String question,
        String answer,
        String status,
        String feedbackRating,
        LocalDateTime createdAt,
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