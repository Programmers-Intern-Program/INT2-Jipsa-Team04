package com.jipsa.upload;

import com.jipsa.file.File;
import com.jipsa.file.FileRepository;
import com.jipsa.file.S3Service;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.List;

@Component
@ConditionalOnProperty(name = "app.upload.reconcile.enabled", havingValue = "true", matchIfMissing = true)
public class UploadReconciler {

    private static final Logger log = LoggerFactory.getLogger(UploadReconciler.class);

    private final UploadsRepository uploadsRepository;
    private final FileRepository fileRepository;
    private final S3Service s3Service;
    private final TransactionTemplate transactionTemplate;
    private final String bucket;
    private final long staleMs;

    public UploadReconciler(UploadsRepository uploadsRepository,
                            FileRepository fileRepository,
                            S3Service s3Service,
                            PlatformTransactionManager transactionManager,
                            @Value("${app.s3.bucket}") String bucket,
                            @Value("${app.upload.reconcile.stale-ms:900000}") long staleMs) {
        this.uploadsRepository = uploadsRepository;
        this.fileRepository = fileRepository;
        this.s3Service = s3Service;
        this.transactionTemplate = new TransactionTemplate(transactionManager);
        this.bucket = bucket;
        this.staleMs = staleMs;
    }

    @Scheduled(fixedDelayString = "${app.upload.reconcile.interval-ms:300000}")
    public void reconcile() {
        LocalDateTime cutoff = LocalDateTime.now().minus(Duration.ofMillis(staleMs));
        for (Uploads batch : uploadsRepository.findByStatusAndCreatedAtBefore(UploadStatus.UPLOADING, cutoff)) {
            try {
                reconcileBatch(batch.getId());
            } catch (RuntimeException e) {
                log.error("Failed to reconcile stuck upload batch {}", batch.getId(), e);
            }
        }
    }

    private void reconcileBatch(Long uploadsId) {
        List<File> files = fileRepository.findByUploadsId(uploadsId);
        for (File file : files) {
            if (file.getS3Key() != null && !file.getS3Key().isBlank()) {
                deleteQuietly(file.getS3Key());
            }
        }
        List<Long> fileIds = files.stream().map(File::getId).toList();
        transactionTemplate.executeWithoutResult(status -> {
            fileRepository.deleteAllById(fileIds);
            uploadsRepository.findById(uploadsId).ifPresent(uploads -> {
                uploads.setStatus(UploadStatus.FAILED);
                uploads.setFinishedAt(LocalDateTime.now());
            });
        });
        log.warn("Reconciled stuck upload batch {} ({} orphan file(s) removed)", uploadsId, files.size());
    }

    private void deleteQuietly(String key) {
        try {
            s3Service.delete(bucket, key);
        } catch (RuntimeException e) {
            log.warn("S3 정리 실패 - 고아 객체 가능(key={}): {}", key, e.toString());
        }
    }
}