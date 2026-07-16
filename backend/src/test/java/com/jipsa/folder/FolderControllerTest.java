package com.jipsa.folder;

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

import java.util.List;

import static org.hamcrest.Matchers.nullValue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyBoolean;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * FolderController 웹 레이어 슬라이스 테스트. FolderService는 mock으로 대체하고
 * 요청/응답 JSON 스펙과 예외 -> HTTP 상태 매핑(GlobalExceptionHandler)만 검증한다.
 *
 * 인증 주입 방식: UploadControllerTest와 동일하게 CurrentUserProvider를 mock으로 대체하고,
 * 각 테스트에서 필요할 때 given(currentUserProvider.requireUserId())로 사용자 ID를 스텁한다.
 */
@WebMvcTest(FolderController.class)
@AutoConfigureMockMvc(addFilters = false)
@Import(com.jipsa.common.GlobalExceptionHandler.class)
class FolderControllerTest {

    private static final Long USER_ID = 1L;

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private FolderService folderService;

    @MockitoBean
    private CurrentUserProvider currentUserProvider;

    // @WebMvcTest는 Filter 타입 빈도 슬라이스에 포함시키기 때문에 JwtAuthenticationFilter가
    // 같이 뜨고, 그 생성자가 JwtService를 요구한다. addFilters = false라 실제로 인증에
    // 쓰이진 않지만, 컨텍스트를 띄우려면 빈 자체는 있어야 해서 mock으로 채워준다.
    @MockitoBean
    private JwtService jwtService;

    @Test
    void list_본인_폴더_평면목록을_반환한다() throws Exception {
        given(currentUserProvider.requireUserId()).willReturn(USER_ID);
        given(folderService.list(USER_ID)).willReturn(List.of(
                new FolderResponse(1L, "재무 보고서", null),
                new FolderResponse(2L, "2024년", 1L)
        ));

        mockMvc.perform(get("/api/v1/folders"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.folders.length()").value(2))
                .andExpect(jsonPath("$.folders[0].folderId").value(1))
                .andExpect(jsonPath("$.folders[0].name").value("재무 보고서"))
                .andExpect(jsonPath("$.folders[0].parentFolderId").value(nullValue()))
                .andExpect(jsonPath("$.folders[1].parentFolderId").value(1));
    }

    @Test
    void create_생성된_folderId와_201을_반환한다() throws Exception {
        given(currentUserProvider.requireUserId()).willReturn(USER_ID);
        given(folderService.create(USER_ID, "새 폴더", null)).willReturn(10L);

        mockMvc.perform(post("/api/v1/folders")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"새 폴더\"}"))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.folderId").value(10));
    }

    @Test
    void create_이름이_비어있으면_400() throws Exception {
        mockMvc.perform(post("/api/v1/folders")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"\"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void update_name만_보내면_parentFolderId는_미변경으로_전달된다() throws Exception {
        given(currentUserProvider.requireUserId()).willReturn(USER_ID);

        mockMvc.perform(patch("/api/v1/folders/{id}", 5)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"새 이름\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.success").value(true));

        verify(folderService).update(USER_ID, 5L, "새 이름", true, null, false);
    }

    @Test
    void update_parentFolderId를_null로_명시하면_루트이동으로_전달된다() throws Exception {
        given(currentUserProvider.requireUserId()).willReturn(USER_ID);

        mockMvc.perform(patch("/api/v1/folders/{id}", 5)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"parentFolderId\": null}"))
                .andExpect(status().isOk());

        verify(folderService).update(USER_ID, 5L, null, false, null, true);
    }

    @Test
    void update_빈body는_아무것도_바꾸지않음으로_전달된다() throws Exception {
        given(currentUserProvider.requireUserId()).willReturn(USER_ID);

        mockMvc.perform(patch("/api/v1/folders/{id}", 5)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isOk());

        verify(folderService).update(USER_ID, 5L, null, false, null, false);
    }

    @Test
    void update_대상폴더가_없으면_404() throws Exception {
        given(currentUserProvider.requireUserId()).willReturn(USER_ID);
        doThrow(new FolderNotFoundException(99L))
                .when(folderService).update(eq(USER_ID), eq(99L), any(), anyBoolean(), any(), anyBoolean());

        mockMvc.perform(patch("/api/v1/folders/{id}", 99)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isNotFound());
    }

    @Test
    void update_순환참조면_400() throws Exception {
        given(currentUserProvider.requireUserId()).willReturn(USER_ID);
        doThrow(new FolderCircularReferenceException(1L, 2L))
                .when(folderService).update(eq(USER_ID), eq(1L), any(), anyBoolean(), eq(2L), eq(true));

        mockMvc.perform(patch("/api/v1/folders/{id}", 1)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"parentFolderId\": 2}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void delete_성공시_success_true() throws Exception {
        given(currentUserProvider.requireUserId()).willReturn(USER_ID);

        mockMvc.perform(delete("/api/v1/folders/{id}", 3))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.success").value(true));

        verify(folderService).delete(USER_ID, 3L);
    }

    @Test
    void delete_대상폴더가_없으면_404() throws Exception {
        given(currentUserProvider.requireUserId()).willReturn(USER_ID);
        doThrow(new FolderNotFoundException(3L)).when(folderService).delete(USER_ID, 3L);

        mockMvc.perform(delete("/api/v1/folders/{id}", 3))
                .andExpect(status().isNotFound());
    }
}
