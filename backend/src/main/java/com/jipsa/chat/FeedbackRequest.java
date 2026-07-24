package com.jipsa.chat;

public record FeedbackRequest(
        String rating,
        String comment
) {
}