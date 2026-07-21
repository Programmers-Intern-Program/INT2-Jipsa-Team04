package com.jipsa.upload;

import com.jipsa.common.exception.UnsupportedFileTypeException;
import com.jipsa.common.exception.UploadLimitExceededException;
import com.jipsa.file.File;
import com.jipsa.file.FileRepository;
import com.jipsa.file.S3Service;
import com.jipsa.folder.Folder;
import com.jipsa.folder.FolderNotFoundException;
import com.jipsa.folder.FolderRepository;
import com.jipsa.job.JobService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.Mockito;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
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
    private FolderRepository folderRepository;
    @Mock
    private S3Service s3Service;
    @Mock
    private JobService jobService;
    @Mock
    private PlatformTransactionManager transactionManager;

    private UploadService uploadService;

    @BeforeEach
    void setUp() {
        uploadService = new UploadService(uploadsRepository, fileRepository, folderRepository,
                s3Service, jobService, transactionManager, "test-bucket", 107374182400L);
    }

    private MockMultipartFile pdf(String name) {
        return new MockMultipartFile("files", name, "application/pdf", "content".getBytes());
    }

    private void stubBatchAndFile() {
        when(uploadsRepository.save(any(Uploads.class))).thenAnswer(inv -> {
            Uploads u = inv.getArgument(0);
            u.setId(10L);
            return u;
        });
        when(uploadsRepository.findById(10L)).thenReturn(Optional.of(new Uploads()));
        when(fileRepository.save(any(File.class))).thenAnswer(inv -> {
            File f = inv.getArgument(0);
            f.setId(100L);
            return f;
        });
        when(s3Service.newKey()).thenReturn("files/generated-key");
    }

    @Test
    void uploadCleansUpFileAndS3WhenPutFails() {
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
        when(s3Service.newKey()).thenReturn("files/key-x");
        doThrow(new RuntimeException("s3 down"))
                .when(s3Service).upload(eq("test-bucket"), eq("files/key-x"), any(MultipartFile.class));

        assertThatThrownBy(() -> uploadService.upload(1L, List.of(pdf("test.pdf")), null))
                .isInstanceOf(RuntimeException.class);

        verify(s3Service).delete("test-bucket", "files/key-x");
        verify(fileRepository).deleteAllById(List.of(100L));
    }

    @Test
    void uploadStoresFileAndEnqueuesJob() {
        stubBatchAndFile();

        UploadResponse response = uploadService.upload(1L, List.of(pdf("test.pdf")), null);

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
    void uploadReturnsExistingBatchForSameIdempotencyKey() {
        Uploads existing = new Uploads();
        existing.setId(42L);
        when(uploadsRepository.findByUsersIdAndIdempotencyKey(1L, "key-1")).thenReturn(Optional.of(existing));
        when(fileRepository.findIdsByUploadsId(42L)).thenReturn(List.of(100L, 101L));

        UploadResponse response = uploadService.upload(1L, List.of(pdf("test.pdf")), null, "key-1");

        assertThat(response.uploadId()).isEqualTo(42L);
        assertThat(response.fileIds()).containsExactly(100L, 101L);
        verify(s3Service, never()).upload(any(), any());
    }

    @Test
    void uploadIntoFolderSetsFolderId() {
        when(folderRepository.findByIdAndUsersId(7L, 1L)).thenReturn(Optional.of(new Folder()));
        stubBatchAndFile();

        uploadService.upload(1L, List.of(pdf("test.pdf")), 7L);

        ArgumentCaptor<File> fileCaptor = ArgumentCaptor.forClass(File.class);
        verify(fileRepository).save(fileCaptor.capture());
        assertThat(fileCaptor.getValue().getFolderId()).isEqualTo(7L);
    }

    @Test
    void rejectsUnknownFolder() {
        when(folderRepository.findByIdAndUsersId(7L, 1L)).thenReturn(Optional.empty());

        assertThatThrownBy(() -> uploadService.upload(1L, List.of(pdf("test.pdf")), 7L))
                .isInstanceOf(FolderNotFoundException.class);

        verify(s3Service, never()).upload(any(), any());
    }

    @Test
    void rejectsMoreThanFiveFiles() {
        List<MultipartFile> files = List.of(
                pdf("1.pdf"), pdf("2.pdf"), pdf("3.pdf"),
                pdf("4.pdf"), pdf("5.pdf"), pdf("6.pdf"));

        assertThatThrownBy(() -> uploadService.upload(1L, files, null))
                .isInstanceOf(UploadLimitExceededException.class);

        verify(s3Service, never()).upload(any(), any());
    }

    @Test
    void rejectsOversizedFile() {
        MultipartFile big = Mockito.mock(MultipartFile.class);
        when(big.isEmpty()).thenReturn(false);
        when(big.getSize()).thenReturn(21L * 1024 * 1024);
        when(big.getOriginalFilename()).thenReturn("big.pdf");

        assertThatThrownBy(() -> uploadService.upload(1L, List.of(big), null))
                .isInstanceOf(UploadLimitExceededException.class);

        verify(s3Service, never()).upload(any(), any());
    }

    @Test
    void rejectsUnsupportedType() {
        assertThatThrownBy(() -> uploadService.upload(1L, List.of(pdf("malware.exe")), null))
                .isInstanceOf(UnsupportedFileTypeException.class);

        verify(s3Service, never()).upload(any(), any());
    }
}