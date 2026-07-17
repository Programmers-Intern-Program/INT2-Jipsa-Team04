package com.jipsa.admin;

import jakarta.validation.constraints.NotBlank;

/** PATCH /api/v1/admin/users/{id}/role 요청. role은 "ADMIN" 또는 "USERS"(DDL CK_Users_Role 기준). */
public record UpdateRoleRequest(@NotBlank String role) {
}
