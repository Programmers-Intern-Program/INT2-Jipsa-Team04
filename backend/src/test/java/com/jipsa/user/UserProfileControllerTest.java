package com.jipsa.user;

import com.jipsa.auth.JwtService;
import com.jipsa.auth.UserRoleCache;
import com.jipsa.common.CurrentUserProvider;
import com.jipsa.common.NotFoundException;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import static org.mockito.BDDMockito.given;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * UserProfileController 웹 레이어 슬라이스 테스트. UserSettingControllerTest와 동일하게
 * CurrentUserProvider를 mock으로 대체하고, GlobalExceptionHandler를 Import해 예외 → HTTP 매핑을 검증한다.
 */
@WebMvcTest(UserProfileController.class)
@AutoConfigureMockMvc(addFilters = false)
@Import(com.jipsa.common.GlobalExceptionHandler.class)
class UserProfileControllerTest {

    private static final Long USER_ID = 1L;

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private UserProfileService userProfileService;

    @MockitoBean
    private CurrentUserProvider currentUserProvider;

    // @WebMvcTest가 Filter 타입 빈(JwtAuthenticationFilter)을 슬라이스에 포함시키므로 그 의존성인
    // JwtService/UserRoleCache/UsersRepository도 mock으로 채워줘야 컨텍스트가 뜬다(addFilters=false라
    // 실제 인증엔 안 쓰인다).
    @MockitoBean
    private JwtService jwtService;

    @MockitoBean
    private UserRoleCache userRoleCache;

    @MockitoBean
    private UsersRepository usersRepository;

    @Test
    void me_본인_프로필을_공통포맷으로_반환한다() throws Exception {
        given(currentUserProvider.requireUserId()).willReturn(USER_ID);
        given(userProfileService.getMe(USER_ID)).willReturn(
                new MeResponse(USER_ID, "홍길동", "https://img.example/p.png", "USERS", "ACTIVE"));

        mockMvc.perform(get("/api/v1/users/me"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.success").value(true))
                .andExpect(jsonPath("$.data.userId").value(1))
                .andExpect(jsonPath("$.data.name").value("홍길동"))
                .andExpect(jsonPath("$.data.profileImageUrl").value("https://img.example/p.png"))
                .andExpect(jsonPath("$.data.role").value("USERS"))
                .andExpect(jsonPath("$.data.status").value("ACTIVE"));
    }

    @Test
    void me_사용자가_없으면_404() throws Exception {
        given(currentUserProvider.requireUserId()).willReturn(USER_ID);
        given(userProfileService.getMe(USER_ID))
                .willThrow(new NotFoundException("사용자를 찾을 수 없습니다."));

        mockMvc.perform(get("/api/v1/users/me"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.success").value(false))
                .andExpect(jsonPath("$.error.code").value("NOT_FOUND"));
    }
}
