package com.jipsa.upload;

import java.time.LocalDateTime;

public record UploadStatusResponse(
        UploadStatus status,
        Integer total,
        LocalDateTime createdAt,
        LocalDateTime finishedAt
) {
}