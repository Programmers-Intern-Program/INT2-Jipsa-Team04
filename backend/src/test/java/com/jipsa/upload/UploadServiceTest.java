package com.jipsa.upload;

import com.jipsa.common.exception.UnsupportedFileTypeException;
import com.jipsa.common.exception.UploadLimitExceededException;
import com.jipsa.file.File;
import com.jipsa.file.FileRepository;
import com.jipsa.file.S3Service;
import com.jipsa.job.JobService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.Mockito;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class UploadServiceTest {

    @Mock
    private UploadsRepository uploadsRepository;
    @Mock
    private FileRepository fileRepository;
    @Mock
    private S3Service s3Service;
    @Mock
    private JobService jobService;

    private UploadService uploadService;

    @BeforeEach
    void setUp() {
        uploadService = new UploadService(uploadsRepository, fileRepository,
                s3Service, jobService, "test-bucket");
    }

    private MockMultipartFile pdf(String name) {
        return new MockMultipartFile("files", name, "application/pdf", "content".getBytes());
    }

    @Test
    void uploadStoresFileAndEnqueuesJob() {
        when(uploadsRepository.save(any(Uploads.class))).thenAnswer(inv -> {
            Uploads u = inv.getArgument(0);
            u.setId(10L);
            return u;
        });
        when(fileRepository.save(any(File.class))).thenAnswer(inv -> {
            File f = inv.getArgument(0);
            f.setId(100L);
            return f;
        });
        when(s3Service.upload(eq("test-bucket"), any(MultipartFile.class)))
                .thenReturn("files/generated-key");

        UploadResponse response = uploadService.upload(1L, List.of(pdf("test.pdf")));

        assertThat(response.uploadId()).isEqualTo(10L);
        assertThat(response.fileIds()).containsExactly(100L);

        ArgumentCaptor<File> fileCaptor = ArgumentCaptor.forClass(File.class);
        verify(fileRepository).save(fileCaptor.capture());
        File saved = fileCaptor.getValue();
        assertThat(saved.getUsersId()).isEqualTo(1L);
        assertThat(saved.getFolderId()).isNull();
        assertThat(saved.getUploadsId()).isEqualTo(10L);
        assertThat(saved.getS3Key()).isEqualTo("files/generated-key");
        assertThat(saved.getFileType()).isEqualTo("pdf");

        verify(jobService).enqueueIngest(100L, 10L);
    }

    @Test
    void rejectsMoreThanFiveFiles() {
        List<MultipartFile> files = List.of(
                pdf("1.pdf"), pdf("2.pdf"), pdf("3.pdf"),
                pdf("4.pdf"), pdf("5.pdf"), pdf("6.pdf"));

        assertThatThrownBy(() -> uploadService.upload(1L, files))
                .isInstanceOf(UploadLimitExceededException.class);

        verify(s3Service, never()).upload(any(), any());
    }

    @Test
    void rejectsOversizedFile() {
        MultipartFile big = Mockito.mock(MultipartFile.class);
        when(big.isEmpty()).thenReturn(false);
        when(big.getSize()).thenReturn(21L * 1024 * 1024);
        when(big.getOriginalFilename()).thenReturn("big.pdf");

        assertThatThrownBy(() -> uploadService.upload(1L, List.of(big)))
                .isInstanceOf(UploadLimitExceededException.class);

        verify(s3Service, never()).upload(any(), any());
    }

    @Test
    void rejectsUnsupportedType() {
        assertThatThrownBy(() -> uploadService.upload(1L, List.of(pdf("malware.exe"))))
                .isInstanceOf(UnsupportedFileTypeException.class);

        verify(s3Service, never()).upload(any(), any());
    }
}