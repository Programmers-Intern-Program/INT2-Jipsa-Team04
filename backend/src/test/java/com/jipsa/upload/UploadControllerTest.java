package com.jipsa.upload;

import com.jipsa.auth.JwtService;
import com.jipsa.common.CurrentUserProvider;
import com.jipsa.common.exception.UploadLimitExceededException;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import java.util.List;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(UploadController.class)
@AutoConfigureMockMvc(addFilters = false)
class UploadControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private UploadService uploadService;

    @MockitoBean
    private CurrentUserProvider currentUserProvider;

    @MockitoBean
    private JwtService jwtService;

    @Test
    void uploadReturnsCreatedWithIds() throws Exception {
        when(currentUserProvider.requireUserId()).thenReturn(1L);
        when(uploadService.upload(anyLong(), any()))
                .thenReturn(new UploadResponse(10L, List.of(100L, 101L)));

        MockMultipartFile file = new MockMultipartFile(
                "files", "test.pdf", "application/pdf", "content".getBytes());

        mockMvc.perform(multipart("/api/v1/uploads").file(file))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.uploadId").value(10))
                .andExpect(jsonPath("$.fileIds[0]").value(100));
    }

    @Test
    void uploadReturns400WhenLimitExceeded() throws Exception {
        when(currentUserProvider.requireUserId()).thenReturn(1L);
        when(uploadService.upload(anyLong(), any()))
                .thenThrow(new UploadLimitExceededException("한 번에 최대 5개까지 업로드할 수 있습니다."));

        MockMultipartFile file = new MockMultipartFile(
                "files", "test.pdf", "application/pdf", "content".getBytes());

        mockMvc.perform(multipart("/api/v1/uploads").file(file))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.success").value(false))
                .andExpect(jsonPath("$.error.code").value("UPLOAD_LIMIT_EXCEEDED"));
    }
}