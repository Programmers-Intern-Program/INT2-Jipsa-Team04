package com.jipsa.upload;

import com.jipsa.file.FileStatus;

import java.time.LocalDateTime;

public record RecentUploadItem(
        Long fileId,
        String name,
        String fileType,
        Long sizeBytes,
        FileStatus status,
        String errorMessage,
        LocalDateTime createdAt
) {
}