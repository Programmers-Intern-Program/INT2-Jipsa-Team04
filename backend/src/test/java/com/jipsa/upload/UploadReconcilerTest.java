package com.jipsa.upload;

import com.jipsa.file.File;
import com.jipsa.file.FileRepository;
import com.jipsa.file.S3Service;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.transaction.PlatformTransactionManager;

import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class UploadReconcilerTest {

    @Mock
    private UploadsRepository uploadsRepository;
    @Mock
    private FileRepository fileRepository;
    @Mock
    private S3Service s3Service;
    @Mock
    private PlatformTransactionManager transactionManager;

    private UploadReconciler reconciler;

    @BeforeEach
    void setUp() {
        reconciler = new UploadReconciler(uploadsRepository, fileRepository, s3Service,
                transactionManager, "test-bucket", 900000L);
    }

    @Test
    void reconcileFailsStuckBatchAndDeletesObjects() {
        Uploads stuck = new Uploads();
        stuck.setId(10L);
        File orphan = new File();
        orphan.setId(100L);
        orphan.setS3Key("files/orphan");
        when(uploadsRepository.findByStatusAndCreatedAtBefore(any(), any())).thenReturn(List.of(stuck));
        when(fileRepository.findByUploadsId(10L)).thenReturn(List.of(orphan));
        when(uploadsRepository.findById(10L)).thenReturn(Optional.of(stuck));

        reconciler.reconcile();

        verify(s3Service).delete("test-bucket", "files/orphan");
        verify(fileRepository).deleteAllById(List.of(100L));
        assertThat(stuck.getStatus()).isEqualTo(UploadStatus.FAILED);
    }
}