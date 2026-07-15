package com.jipsa.admin;

import jakarta.validation.constraints.NotBlank;

/** POST /api/v1/admin/users/{id}/unsuspend 요청. */
public record UnsuspendUserRequest(@NotBlank String liftedReason) {
}
