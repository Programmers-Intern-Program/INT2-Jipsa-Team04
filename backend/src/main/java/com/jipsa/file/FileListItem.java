package com.jipsa.file;

import java.time.LocalDateTime;

public record FileListItem(
        Long fileId,
        String name,
        String fileType,
        Long sizeBytes,
        Long folderId,
        FileStatus status,
        boolean star,
        LocalDateTime modifiedAt
) {
}