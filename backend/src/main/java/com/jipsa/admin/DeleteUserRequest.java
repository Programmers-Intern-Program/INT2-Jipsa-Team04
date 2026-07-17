package com.jipsa.admin;

import jakarta.validation.constraints.NotBlank;

/** DELETE /api/v1/admin/users/{id} 요청 본문. */
public record DeleteUserRequest(@NotBlank String reason) {
}
