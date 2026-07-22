package com.jipsa.organize;

import com.jipsa.auth.JwtService;
import com.jipsa.common.BadRequestException;
import com.jipsa.common.CurrentUserProvider;
import com.jipsa.common.exception.FileNotFoundException;
import com.jipsa.common.exception.ForbiddenException;
import com.jipsa.folder.FolderNotFoundException;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import java.util.List;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * OrganizeController 웹 레이어 슬라이스 테스트. OrganizeService는 mock으로 대체하고
 * 요청/응답 JSON 스펙과 예외 -> HTTP 상태 매핑(GlobalExceptionHandler)만 검증한다.
 * FolderControllerTest와 동일한 방식(CurrentUserProvider mock, addFilters=false).
 */
@WebMvcTest(OrganizeController.class)
@AutoConfigureMockMvc(addFilters = false)
@Import(com.jipsa.common.GlobalExceptionHandler.class)
class OrganizeControllerTest {

    private static final Long USER_ID = 1L;

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private OrganizeService organizeService;

    @MockitoBean
    private CurrentUserProvider currentUserProvider;

    // @WebMvcTest가 JwtAuthenticationFilter까지 슬라이스에 포함시켜서 빈이 필요하다
    // (FolderControllerTest와 동일한 이유 — addFilters=false라 실제 인증엔 안 쓰임).
    @MockitoBean
    private JwtService jwtService;

    @Test
    void currentTree_현재_폴더_트리를_반환한다() throws Exception {
        given(currentUserProvider.requireUserId()).willReturn(USER_ID);
        given(organizeService.getCurrentFolderTree(USER_ID)).willReturn(List.of(
                new FolderTreeNode(1L, "루트", List.of(
                        new FolderTreeNode(2L, "자식", List.of())))));

        mockMvc.perform(get("/api/v1/organize/current-tree"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.folders.length()").value(1))
                .andExpect(jsonPath("$.folders[0].folderId").value(1))
                .andExpect(jsonPath("$.folders[0].name").value("루트"))
                .andExpect(jsonPath("$.folders[0].children[0].folderId").value(2));
    }

    @Test
    void propose_AI_제안을_그대로_반환한다() throws Exception {
        given(currentUserProvider.requireUserId()).willReturn(USER_ID);
        given(organizeService.generateProposal(USER_ID)).willReturn(new OrganizeProposal(
                List.of(new ProposedFolder("t1", "제안폴더", null, null)),
                List.of(new FileMapping(10L, null, "t1", "새이름.pdf"))));

        mockMvc.perform(post("/api/v1/organize/propose"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.newFolders[0].tempId").value("t1"))
                .andExpect(jsonPath("$.newFolders[0].name").value("제안폴더"))
                .andExpect(jsonPath("$.mappings[0].fileId").value(10))
                .andExpect(jsonPath("$.mappings[0].targetTempId").value("t1"));
    }

    @Test
    void apply_성공하면_success_true를_반환하고_요청바디를_그대로_전달한다() throws Exception {
        given(currentUserProvider.requireUserId()).willReturn(USER_ID);
        given(organizeService.applyProposal(eq(USER_ID), any()))
                .willReturn(new OrganizeApplyResponse(true, List.of()));

        String body = """
                {
                  "newFolders": [
                    {"tempId": "t1", "name": "제안폴더", "parentTempId": null, "parentFolderId": null}
                  ],
                  "mappings": [
                    {"fileId": 10, "targetFolderId": null, "targetTempId": "t1", "newName": "새이름.pdf"}
                  ]
                }
                """;

        mockMvc.perform(post("/api/v1/organize/apply")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.success").value(true))
                .andExpect(jsonPath("$.held").isEmpty());

        verify(organizeService).applyProposal(eq(USER_ID), eq(new OrganizeProposal(
                List.of(new ProposedFolder("t1", "제안폴더", null, null)),
                List.of(new FileMapping(10L, null, "t1", "새이름.pdf")))));
    }

    @Test
    void apply_보류된_매핑이_있으면_held로_반환한다() throws Exception {
        given(currentUserProvider.requireUserId()).willReturn(USER_ID);
        given(organizeService.applyProposal(eq(USER_ID), any()))
                .willReturn(new OrganizeApplyResponse(true,
                        List.of(new FileMapping(10L, 5L, null, null, 0.2))));

        mockMvc.perform(post("/api/v1/organize/apply")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"newFolders\":[],\"mappings\":[]}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.success").value(true))
                .andExpect(jsonPath("$.held[0].fileId").value(10))
                .andExpect(jsonPath("$.held[0].confidence").value(0.2));
    }

    @Test
    void apply_존재하지않는_폴더면_404() throws Exception {
        given(currentUserProvider.requireUserId()).willReturn(USER_ID);
        doThrow(new FolderNotFoundException(99L))
                .when(organizeService).applyProposal(eq(USER_ID), any());

        mockMvc.perform(post("/api/v1/organize/apply")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"newFolders\":[],\"mappings\":[]}"))
                .andExpect(status().isNotFound());
    }

    @Test
    void apply_존재하지않는_파일이면_404() throws Exception {
        given(currentUserProvider.requireUserId()).willReturn(USER_ID);
        doThrow(new FileNotFoundException("파일을 찾을 수 없습니다: 10"))
                .when(organizeService).applyProposal(eq(USER_ID), any());

        mockMvc.perform(post("/api/v1/organize/apply")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"newFolders\":[],\"mappings\":[]}"))
                .andExpect(status().isNotFound());
    }

    @Test
    void apply_다른사람_파일이면_403() throws Exception {
        given(currentUserProvider.requireUserId()).willReturn(USER_ID);
        doThrow(new ForbiddenException("해당 파일에 접근할 권한이 없습니다: 10"))
                .when(organizeService).applyProposal(eq(USER_ID), any());

        mockMvc.perform(post("/api/v1/organize/apply")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"newFolders\":[],\"mappings\":[]}"))
                .andExpect(status().isForbidden());
    }

    @Test
    void apply_잘못된_매핑이면_400() throws Exception {
        given(currentUserProvider.requireUserId()).willReturn(USER_ID);
        doThrow(new BadRequestException("targetFolderId/targetTempId 중 하나만 지정할 수 있습니다: fileId=10"))
                .when(organizeService).applyProposal(eq(USER_ID), any());

        mockMvc.perform(post("/api/v1/organize/apply")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"newFolders\":[],\"mappings\":[]}"))
                .andExpect(status().isBadRequest());
    }
}
