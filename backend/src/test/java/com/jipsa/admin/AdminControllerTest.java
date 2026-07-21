package com.jipsa.admin;

import com.jipsa.auth.JwtService;
import com.jipsa.common.CurrentUserProvider;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import java.util.List;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * AdminController 웹 레이어 슬라이스 테스트. ADMIN 권한 게이트(@PreAuthorize)는 필터/메서드보안
 * 빈이 로드되지 않는 이 슬라이스에서 검증 대상이 아니다 — 실제 토큰으로 ADMIN=200/USERS=403을
 * 확인하는 건 {@code AdminAuthorizationIntegrationTest} 쪽. 여기서는 요청 검증·응답 매핑·
 * 서비스 예외 → HTTP 상태 매핑만 본다.
 */
@WebMvcTest(AdminController.class)
@AutoConfigureMockMvc(addFilters = false)
class AdminControllerTest {

    private static final Long ADMIN_ID = 1L;
    private static final Long TARGET_ID = 2L;

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private AdminService adminService;

    @MockitoBean
    private CurrentUserProvider currentUserProvider;

    @MockitoBean
    private JwtService jwtService;

    @Test
    void list_사용자목록과_총개수를반환한다() throws Exception {
        when(currentUserProvider.requireUserId()).thenReturn(ADMIN_ID);
        when(adminService.listUsers(ADMIN_ID, null, null)).thenReturn(new AdminUserListResponse(
                List.of(new AdminUserListItem(TARGET_ID, "USERS", "ACTIVE", false, null, 3L, null)), 1L));

        mockMvc.perform(get("/api/v1/admin/users"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.total").value(1))
                .andExpect(jsonPath("$.items[0].userId").value(2))
                .andExpect(jsonPath("$.items[0].documentCount").value(3));
    }

    @Test
    void suspend_성공시_success_true() throws Exception {
        when(currentUserProvider.requireUserId()).thenReturn(ADMIN_ID);

        mockMvc.perform(post("/api/v1/admin/users/{id}/suspend", TARGET_ID)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"sanctionType\":\"TEMP_SUSPEND\",\"reason\":\"약관 위반 신고 누적\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.success").value(true));

        verify(adminService).suspend(eq(ADMIN_ID), eq(TARGET_ID), any(SuspendUserRequest.class));
    }

    @Test
    void suspend_사유가없으면_400() throws Exception {
        when(currentUserProvider.requireUserId()).thenReturn(ADMIN_ID);

        mockMvc.perform(post("/api/v1/admin/users/{id}/suspend", TARGET_ID)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"sanctionType\":\"TEMP_SUSPEND\",\"reason\":\"\"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void suspend_만료일시가과거면_400() throws Exception {
        when(currentUserProvider.requireUserId()).thenReturn(ADMIN_ID);

        mockMvc.perform(post("/api/v1/admin/users/{id}/suspend", TARGET_ID)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"sanctionType\":\"TEMP_SUSPEND\",\"reason\":\"사유\",\"expiresAt\":\"2000-01-01T00:00:00\"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void suspend_자기자신대상이면_400() throws Exception {
        when(currentUserProvider.requireUserId()).thenReturn(ADMIN_ID);
        doThrow(new SelfTargetNotAllowedException())
                .when(adminService).suspend(eq(ADMIN_ID), eq(ADMIN_ID), any(SuspendUserRequest.class));

        mockMvc.perform(post("/api/v1/admin/users/{id}/suspend", ADMIN_ID)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"sanctionType\":\"TEMP_SUSPEND\",\"reason\":\"사유\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error.code").value("SELF_TARGET_NOT_ALLOWED"));
    }

    @Test
    void suspend_이미정지된사용자면_409() throws Exception {
        when(currentUserProvider.requireUserId()).thenReturn(ADMIN_ID);
        doThrow(new AdminActionConflictException("이미 정지된 사용자입니다: " + TARGET_ID))
                .when(adminService).suspend(eq(ADMIN_ID), eq(TARGET_ID), any(SuspendUserRequest.class));

        mockMvc.perform(post("/api/v1/admin/users/{id}/suspend", TARGET_ID)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"sanctionType\":\"TEMP_SUSPEND\",\"reason\":\"사유\"}"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.error.code").value("ADMIN_ACTION_CONFLICT"));
    }

    @Test
    void unsuspend_성공시_success_true() throws Exception {
        when(currentUserProvider.requireUserId()).thenReturn(ADMIN_ID);

        mockMvc.perform(post("/api/v1/admin/users/{id}/unsuspend", TARGET_ID)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"liftedReason\":\"오제재 확인됨\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.success").value(true));

        verify(adminService).unsuspend(eq(ADMIN_ID), eq(TARGET_ID), any(UnsuspendUserRequest.class));
    }

    @Test
    void delete_성공시_success_true() throws Exception {
        when(currentUserProvider.requireUserId()).thenReturn(ADMIN_ID);

        mockMvc.perform(delete("/api/v1/admin/users/{id}", TARGET_ID)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"reason\":\"악성 사용자 신고 누적\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.success").value(true));

        verify(adminService).delete(eq(ADMIN_ID), eq(TARGET_ID), any(DeleteUserRequest.class));
    }

    @Test
    void delete_대상없으면_404() throws Exception {
        when(currentUserProvider.requireUserId()).thenReturn(ADMIN_ID);
        doThrow(new AdminUserNotFoundException(TARGET_ID))
                .when(adminService).delete(eq(ADMIN_ID), eq(TARGET_ID), any(DeleteUserRequest.class));

        mockMvc.perform(delete("/api/v1/admin/users/{id}", TARGET_ID)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"reason\":\"악성 사용자 신고 누적\"}"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error.code").value("ADMIN_USER_NOT_FOUND"));
    }

    @Test
    void sanctions_제재이력목록을반환한다() throws Exception {
        when(currentUserProvider.requireUserId()).thenReturn(ADMIN_ID);
        given(adminService.getSanctions(ADMIN_ID, TARGET_ID)).willReturn(new SanctionListResponse(List.of(
                new SanctionItem("TEMP_SUSPEND", "LIFTED", "약관 위반", "ACTIVE",
                        null, null, ADMIN_ID, null, "오제재 확인됨"))));

        mockMvc.perform(get("/api/v1/admin/users/{id}/sanctions", TARGET_ID))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.sanctions.length()").value(1))
                .andExpect(jsonPath("$.sanctions[0].sanctionType").value("TEMP_SUSPEND"));
    }

    @Test
    void role_성공시_success_true() throws Exception {
        when(currentUserProvider.requireUserId()).thenReturn(ADMIN_ID);

        mockMvc.perform(patch("/api/v1/admin/users/{id}/role", TARGET_ID)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"role\":\"ADMIN\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.success").value(true));

        verify(adminService).updateRole(eq(ADMIN_ID), eq(TARGET_ID), any(UpdateRoleRequest.class));
    }

    @Test
    void role_잘못된값이면_400() throws Exception {
        when(currentUserProvider.requireUserId()).thenReturn(ADMIN_ID);
        doThrow(new IllegalArgumentException("role은 ADMIN 또는 USERS만 가능합니다: USER"))
                .when(adminService).updateRole(eq(ADMIN_ID), eq(TARGET_ID), any(UpdateRoleRequest.class));

        mockMvc.perform(patch("/api/v1/admin/users/{id}/role", TARGET_ID)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"role\":\"USER\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error.code").value("BAD_REQUEST"));
    }
}
