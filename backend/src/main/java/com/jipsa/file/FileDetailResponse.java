package com.jipsa.file;

import java.time.LocalDateTime;
import java.util.List;

public record FileDetailResponse(
        String name,
        String fileType,
        Long sizeBytes,
        Long folderId,
        String ownerName,
        boolean star,
        String summary,
        List<String> tags,
        Entities entities,
        LocalDateTime modifiedAt,
        FileStatus status,
        String processingStage,
        String securityRank,
        boolean piiDetected,
        String documentType,
        String extractionStatus,
        Double extractionConfidence
) {
    public record Entities(
            List<String> dates,
            List<String> people,
            List<String> amounts,
            String project
    ) {
    }
}