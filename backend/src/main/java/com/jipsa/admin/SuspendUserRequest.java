package com.jipsa.admin;

import jakarta.validation.constraints.NotBlank;

import java.time.LocalDateTime;

/** POST /api/v1/admin/users/{id}/suspend 요청. sanctionType은 SanctionType 값 문자열. */
public record SuspendUserRequest(
        @NotBlank String sanctionType,
        @NotBlank String reason,
        LocalDateTime expiresAt
) {
}
