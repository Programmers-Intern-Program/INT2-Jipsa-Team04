package com.jipsa.admin;

import com.jipsa.auth.JwtAuthenticationFilter;
import com.jipsa.auth.JwtService;
import com.jipsa.common.CurrentUserProvider;
import com.jipsa.config.SecurityConfig;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import java.util.List;

import static org.mockito.BDDMockito.given;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * {@code @PreAuthorize("hasRole('ADMIN')")}(토큰 role 검사) + {@code AdminController}의
 * {@code @ModelAttribute} 메서드(DB role 재검증, {@link AdminAccessGuard} 호출)로 이뤄진 인가
 * 게이트가 실제 로그인 토큰으로도 의도대로 동작하는지 검증한다. addFilters를 끄지 않고 {@link SecurityConfig}
 * (＋{@code @EnableMethodSecurity})·{@link JwtAuthenticationFilter}·{@link JwtService}·
 * {@link CurrentUserProvider}를 전부 실제 빈으로 태워 토큰 → 인증 → 인가까지 실제 배선을 그대로
 * 거친다. {@link AdminService}(비즈니스 로직)와 {@link AdminAccessGuard}(DB 재검증 결과)만 mock —
 * 여기서 검증하려는 건 "ADMIN role 토큰=200, USERS role 토큰=403", 그리고 "JWT는 ADMIN이어도
 * DB 기준으로 더 이상 ADMIN이 아니면 403"이다.
 *
 * <p>요청 검증·응답 매핑 등 컨트롤러 동작 자체는 {@code AdminControllerTest}(필터 꺼진 슬라이스)
 * 쪽 책임이라 여기서 중복하지 않는다. {@link AdminAccessGuard}의 DB 조회 로직 자체는
 * {@code AdminAccessGuardTest}에서 별도로 검증한다.
 */
@WebMvcTest(AdminController.class)
@Import({SecurityConfig.class, JwtAuthenticationFilter.class, JwtService.class, CurrentUserProvider.class})
class AdminAuthorizationIntegrationTest {

    private static final Long ADMIN_ID = 1L;
    private static final Long TARGET_ID = 2L;

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private JwtService jwtService;

    @MockitoBean
    private AdminService adminService;

    @MockitoBean
    private AdminAccessGuard adminAccessGuard;

    @MockitoBean
    private com.jipsa.user.UsersRepository usersRepository;

    @org.junit.jupiter.api.BeforeEach
    void stubActiveUser() {
        com.jipsa.user.Users activeUser = new com.jipsa.user.Users();
        activeUser.setStatus("ACTIVE");
        given(usersRepository.findByIdAndDelFalse(ADMIN_ID)).willReturn(java.util.Optional.of(activeUser));
    }

    private String tokenFor(String role) {
        return jwtService.generateToken(ADMIN_ID, role);
    }

    // --- GET /api/v1/admin/users ---

    @Test
    void list_ADMIN_토큰이면_200이다() throws Exception {
        given(adminAccessGuard.isCurrentlyAdmin()).willReturn(true);
        given(adminService.listUsers(ADMIN_ID, null, null))
                .willReturn(new AdminUserListResponse(List.of(), 0L));

        mockMvc.perform(get("/api/v1/admin/users")
                        .header("Authorization", "Bearer " + tokenFor("ADMIN")))
                .andExpect(status().isOk());
    }

    @Test
    void list_JWT는_ADMIN이지만_DB에서_role이_회수됐으면_403이다() throws Exception {
        // 리뷰 지적 시나리오 재현: 발급 시점엔 ADMIN이었던 토큰을 그대로 들고 있지만,
        // 그 사이 DB에서 role이 회수된 경우 — 재로그인 없이도 다음 요청부터 바로 막혀야 한다.
        given(adminAccessGuard.isCurrentlyAdmin()).willReturn(false);

        mockMvc.perform(get("/api/v1/admin/users")
                        .header("Authorization", "Bearer " + tokenFor("ADMIN")))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error.code").value("FORBIDDEN"));
    }

    @Test
    void list_USERS_토큰이면_403이다() throws Exception {
        mockMvc.perform(get("/api/v1/admin/users")
                        .header("Authorization", "Bearer " + tokenFor("USERS")))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error.code").value("FORBIDDEN"));
    }

    // --- POST /api/v1/admin/users/{id}/suspend ---

    @Test
    void suspend_ADMIN_토큰이면_200이다() throws Exception {
        given(adminAccessGuard.isCurrentlyAdmin()).willReturn(true);

        mockMvc.perform(post("/api/v1/admin/users/{id}/suspend", TARGET_ID)
                        .header("Authorization", "Bearer " + tokenFor("ADMIN"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"sanctionType\":\"TEMP_SUSPEND\",\"reason\":\"약관 위반\"}"))
                .andExpect(status().isOk());
    }

    @Test
    void suspend_USERS_토큰이면_403이다() throws Exception {
        mockMvc.perform(post("/api/v1/admin/users/{id}/suspend", TARGET_ID)
                        .header("Authorization", "Bearer " + tokenFor("USERS"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"sanctionType\":\"TEMP_SUSPEND\",\"reason\":\"약관 위반\"}"))
                .andExpect(status().isForbidden());
    }

    // --- POST /api/v1/admin/users/{id}/unsuspend ---

    @Test
    void unsuspend_ADMIN_토큰이면_200이다() throws Exception {
        given(adminAccessGuard.isCurrentlyAdmin()).willReturn(true);

        mockMvc.perform(post("/api/v1/admin/users/{id}/unsuspend", TARGET_ID)
                        .header("Authorization", "Bearer " + tokenFor("ADMIN"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"liftedReason\":\"오제재 확인됨\"}"))
                .andExpect(status().isOk());
    }

    @Test
    void unsuspend_USERS_토큰이면_403이다() throws Exception {
        mockMvc.perform(post("/api/v1/admin/users/{id}/unsuspend", TARGET_ID)
                        .header("Authorization", "Bearer " + tokenFor("USERS"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"liftedReason\":\"오제재 확인됨\"}"))
                .andExpect(status().isForbidden());
    }

    // --- DELETE /api/v1/admin/users/{id} ---

    @Test
    void delete_ADMIN_토큰이면_200이다() throws Exception {
        given(adminAccessGuard.isCurrentlyAdmin()).willReturn(true);

        mockMvc.perform(delete("/api/v1/admin/users/{id}", TARGET_ID)
                        .header("Authorization", "Bearer " + tokenFor("ADMIN"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"reason\":\"악성 사용자 신고 누적\"}"))
                .andExpect(status().isOk());
    }

    @Test
    void delete_USERS_토큰이면_403이다() throws Exception {
        mockMvc.perform(delete("/api/v1/admin/users/{id}", TARGET_ID)
                        .header("Authorization", "Bearer " + tokenFor("USERS"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"reason\":\"악성 사용자 신고 누적\"}"))
                .andExpect(status().isForbidden());
    }

    // --- GET /api/v1/admin/users/{id}/sanctions ---

    @Test
    void sanctions_ADMIN_토큰이면_200이다() throws Exception {
        given(adminAccessGuard.isCurrentlyAdmin()).willReturn(true);
        given(adminService.getSanctions(ADMIN_ID, TARGET_ID)).willReturn(new SanctionListResponse(List.of()));

        mockMvc.perform(get("/api/v1/admin/users/{id}/sanctions", TARGET_ID)
                        .header("Authorization", "Bearer " + tokenFor("ADMIN")))
                .andExpect(status().isOk());
    }

    @Test
    void sanctions_USERS_토큰이면_403이다() throws Exception {
        mockMvc.perform(get("/api/v1/admin/users/{id}/sanctions", TARGET_ID)
                        .header("Authorization", "Bearer " + tokenFor("USERS")))
                .andExpect(status().isForbidden());
    }

    // --- PATCH /api/v1/admin/users/{id}/role ---

    @Test
    void role_ADMIN_토큰이면_200이다() throws Exception {
        given(adminAccessGuard.isCurrentlyAdmin()).willReturn(true);

        mockMvc.perform(patch("/api/v1/admin/users/{id}/role", TARGET_ID)
                        .header("Authorization", "Bearer " + tokenFor("ADMIN"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"role\":\"ADMIN\"}"))
                .andExpect(status().isOk());
    }

    @Test
    void role_USERS_토큰이면_403이다() throws Exception {
        mockMvc.perform(patch("/api/v1/admin/users/{id}/role", TARGET_ID)
                        .header("Authorization", "Bearer " + tokenFor("USERS"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"role\":\"ADMIN\"}"))
                .andExpect(status().isForbidden());
    }
}
