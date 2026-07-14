package com.jipsa.file;

public record FileStatusResponse(
        FileStatus status,
        String processingStage,
        Integer attempts,
        String errorMessage
) {
}