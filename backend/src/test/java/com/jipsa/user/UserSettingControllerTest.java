package com.jipsa.user;

import com.jipsa.auth.JwtService;
import com.jipsa.common.CurrentUserProvider;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import java.math.BigDecimal;

import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * UserSettingController 웹 레이어 슬라이스 테스트. UploadControllerTest와 동일하게
 * CurrentUserProvider를 mock으로 대체하고, 각 테스트에서 필요할 때 개별 스텁한다.
 */
@WebMvcTest(UserSettingController.class)
@AutoConfigureMockMvc(addFilters = false)
@Import(com.jipsa.common.GlobalExceptionHandler.class)
class UserSettingControllerTest {

    private static final Long USER_ID = 1L;

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private UserSettingService userSettingService;

    @MockitoBean
    private CurrentUserProvider currentUserProvider;

    // @WebMvcTest는 Filter 타입 빈도 슬라이스에 포함시켜 JwtAuthenticationFilter가 같이 뜬다.
    // addFilters=false라 실제 인증엔 안 쓰이지만, 컨텍스트를 띄우려면 빈 자체는 있어야 해서 mock으로 채운다.
    @MockitoBean
    private JwtService jwtService;

    @Test
    void get_본인_설정을_반환한다() throws Exception {
        given(currentUserProvider.requireUserId()).willReturn(USER_ID);
        UserSetting setting = new UserSetting(USER_ID);
        given(userSettingService.getOrCreate(USER_ID)).willReturn(setting);

        mockMvc.perform(get("/api/v1/users/me/settings"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.sensitivity").value(0.5))
                .andExpect(jsonPath("$.voiceModel").value("OFF"))
                .andExpect(jsonPath("$.responseStyle").value("BALANCED"))
                .andExpect(jsonPath("$.instantSummary").value(true))
                .andExpect(jsonPath("$.autoHighlight").value(true))
                .andExpect(jsonPath("$.pushNotification").value(true));
    }

    @Test
    void update_일부필드만_보내면_나머지는_null로_전달된다() throws Exception {
        given(currentUserProvider.requireUserId()).willReturn(USER_ID);

        mockMvc.perform(patch("/api/v1/users/me/settings")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"sensitivity\": 0.8, \"pushNotification\": false}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.success").value(true));

        PatchUserSettingRequest expected = new PatchUserSettingRequest(
                new BigDecimal("0.8"), null, null, null, null, false);
        verify(userSettingService).update(eq(USER_ID), eq(expected));
    }

    @Test
    void update_빈body도_200() throws Exception {
        given(currentUserProvider.requireUserId()).willReturn(USER_ID);

        mockMvc.perform(patch("/api/v1/users/me/settings")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isOk());

        verify(userSettingService).update(eq(USER_ID),
                eq(new PatchUserSettingRequest(null, null, null, null, null, null)));
    }

    @Test
    void update_sensitivity가_범위밖이면_400() throws Exception {
        mockMvc.perform(patch("/api/v1/users/me/settings")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"sensitivity\": 1.5}"))
                .andExpect(status().isBadRequest());
    }
}
