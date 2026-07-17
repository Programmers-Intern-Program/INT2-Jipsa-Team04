package com.jipsa.admin;

import java.time.LocalDateTime;

/**
 * GET /api/v1/admin/users 목록 항목. lastLoginAt은 Refresh_Tokens 엔티티가 아직 백엔드에
 * 없어 항상 null로 내려간다 — 별도 이슈로 미룬 상태.
 */
public record AdminUserListItem(
        Long userId,
        String role,
        String status,
        boolean del,
        LocalDateTime createdAt,
        long documentCount,
        LocalDateTime lastLoginAt
) {
}
