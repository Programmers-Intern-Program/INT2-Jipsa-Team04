package com.jipsa.admin;

import com.jipsa.common.CurrentUserProvider;
import com.jipsa.common.SuccessResponse;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1/admin/users")
public class AdminController {

    private final AdminService adminService;
    private final CurrentUserProvider currentUserProvider;

    public AdminController(AdminService adminService, CurrentUserProvider currentUserProvider) {
        this.adminService = adminService;
        this.currentUserProvider = currentUserProvider;
    }

    /** GET /api/v1/admin/users — 전체 사용자 목록. */
    @GetMapping
    public AdminUserListResponse list(@RequestParam(required = false) Integer page,
                                       @RequestParam(required = false) Integer size) {
        Long adminId = currentUserProvider.requireUserId();
        return adminService.listUsers(adminId, page, size);
    }

    /** POST /api/v1/admin/users/{id}/suspend — 계정 정지. */
    @PostMapping("/{id}/suspend")
    public SuccessResponse suspend(@PathVariable Long id, @Valid @RequestBody SuspendUserRequest request) {
        Long adminId = currentUserProvider.requireUserId();
        adminService.suspend(adminId, id, request);
        return SuccessResponse.ok();
    }

    /** POST /api/v1/admin/users/{id}/unsuspend — 정지 해제. */
    @PostMapping("/{id}/unsuspend")
    public SuccessResponse unsuspend(@PathVariable Long id, @Valid @RequestBody UnsuspendUserRequest request) {
        Long adminId = currentUserProvider.requireUserId();
        adminService.unsuspend(adminId, id, request);
        return SuccessResponse.ok();
    }

    /** DELETE /api/v1/admin/users/{id} — 관리자에 의한 소프트 삭제. */
    @DeleteMapping("/{id}")
    public SuccessResponse delete(@PathVariable Long id, @Valid @RequestBody DeleteUserRequest request) {
        Long adminId = currentUserProvider.requireUserId();
        adminService.delete(adminId, id, request);
        return SuccessResponse.ok();
    }

    /** GET /api/v1/admin/users/{id}/sanctions — 제재 이력 조회. */
    @GetMapping("/{id}/sanctions")
    public SanctionListResponse sanctions(@PathVariable Long id) {
        Long adminId = currentUserProvider.requireUserId();
        return adminService.getSanctions(adminId, id);
    }

    /** PATCH /api/v1/admin/users/{id}/role — 관리자 권한 부여/해제. */
    @PatchMapping("/{id}/role")
    public SuccessResponse updateRole(@PathVariable Long id, @Valid @RequestBody UpdateRoleRequest request) {
        Long adminId = currentUserProvider.requireUserId();
        adminService.updateRole(adminId, id, request);
        return SuccessResponse.ok();
    }
}
