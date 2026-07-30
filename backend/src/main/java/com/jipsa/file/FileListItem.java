package com.jipsa.file;

import java.time.LocalDateTime;
import java.util.List;

public record FileListItem(
        Long fileId,
        String name,
        String fileType,
        Long sizeBytes,
        Long folderId,
        FileStatus status,
        boolean star,
        LocalDateTime modifiedAt,
        String summary,
        List<String> tags,
        List<String> keywords,
        String securityRank,
        String documentType,
        String extractionStatus
) {
}
