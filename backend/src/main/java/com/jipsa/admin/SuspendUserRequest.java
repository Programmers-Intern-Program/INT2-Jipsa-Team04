package com.jipsa.admin;

import jakarta.validation.constraints.Future;
import jakarta.validation.constraints.NotBlank;

import java.time.LocalDateTime;

/**
 * POST /api/v1/admin/users/{id}/suspend 요청. sanctionType은 SanctionType 값 문자열.
 * expiresAt은 선택 필드라 null이면 검증을 건너뛰고(@Future는 null 허용), 값이 있을 때만
 * 현재 시각보다 미래인지 확인한다 — 과거 시각을 넣으면 만들어지자마자 만료된 정지가 되어
 * 의미가 없기 때문.
 */
public record SuspendUserRequest(
        @NotBlank String sanctionType,
        @NotBlank String reason,
        @Future LocalDateTime expiresAt
) {
}
