package com.jipsa.admin;

import java.time.LocalDateTime;

/** GET /api/v1/admin/users/{id}/sanctions 응답 항목. API 문서.md 2장 필드명 기준. */
public record SanctionItem(
        String sanctionType,
        String sanctionStatus,
        String reason,
        String restoreUserStatus,
        LocalDateTime createdAt,
        LocalDateTime expiresAt,
        Long liftedByAdminId,
        LocalDateTime liftedAt,
        String liftedReason
) {
}
