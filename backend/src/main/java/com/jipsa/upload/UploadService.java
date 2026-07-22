package com.jipsa.upload;

import com.jipsa.common.exception.FileNotFoundException;
import com.jipsa.common.exception.ForbiddenException;
import com.jipsa.common.exception.UnsupportedFileTypeException;
import com.jipsa.common.exception.UploadLimitExceededException;
import com.jipsa.file.File;
import com.jipsa.file.FileRepository;
import com.jipsa.file.S3Service;
import com.jipsa.folder.FolderNotFoundException;
import com.jipsa.folder.FolderRepository;
import com.jipsa.job.JobService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.multipart.MultipartFile;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;

@Service
public class UploadService {

    private static final long MAX_FILE_SIZE = 20L * 1024 * 1024;
    private static final int MAX_FILE_COUNT = 5;
    private static final Set<String> ALLOWED_EXTENSIONS = Set.of(
            "pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "txt", "csv", "hwp", "hwpx", "md");

    private static final Logger log = LoggerFactory.getLogger(UploadService.class);

    private final UploadsRepository uploadsRepository;
    private final FileRepository fileRepository;
    private final FolderRepository folderRepository;
    private final S3Service s3Service;
    private final JobService jobService;
    private final TransactionTemplate transactionTemplate;
    private final String bucket;
    private final long storageQuotaBytes;

    public UploadService(UploadsRepository uploadsRepository,
                         FileRepository fileRepository,
                         FolderRepository folderRepository,
                         S3Service s3Service,
                         JobService jobService,
                         PlatformTransactionManager transactionManager,
                         @Value("${app.s3.bucket}") String bucket,
                         @Value("${app.storage.quota-bytes:107374182400}") long storageQuotaBytes) {
        this.uploadsRepository = uploadsRepository;
        this.fileRepository = fileRepository;
        this.folderRepository = folderRepository;
        this.s3Service = s3Service;
        this.jobService = jobService;
        this.transactionTemplate = new TransactionTemplate(transactionManager);
        this.bucket = bucket;
        this.storageQuotaBytes = storageQuotaBytes;
    }

    public UploadResponse upload(Long userId, List<MultipartFile> files, Long folderId) {
        return upload(userId, files, folderId, null);
    }

    public UploadResponse upload(Long userId, List<MultipartFile> files, Long folderId, String idempotencyKey) {
        Uploads existing = findExistingBatch(userId, idempotencyKey);
        if (existing != null) {
            return new UploadResponse(existing.getId(), fileRepository.findIdsByUploadsId(existing.getId()));
        }
        validate(files);
        if (folderId != null) {
            folderRepository.findByIdAndUsersId(folderId, userId)
                    .orElseThrow(() -> new FolderNotFoundException(folderId));
        }

        long incoming = files.stream().mapToLong(MultipartFile::getSize).sum();
        if (fileRepository.sumSizeBytesByUsersId(userId) + incoming > storageQuotaBytes) {
            throw new UploadLimitExceededException("스토리지 용량을 초과했습니다.");
        }

        Long uploadsId;
        try {
            uploadsId = transactionTemplate.execute(status -> createBatch(userId, files.size(), idempotencyKey));
        } catch (DataIntegrityViolationException e) {
            Uploads raced = findExistingBatch(userId, idempotencyKey);
            if (raced != null) {
                return new UploadResponse(raced.getId(), fileRepository.findIdsByUploadsId(raced.getId()));
            }
            throw e;
        }

        List<PendingUpload> pending = files.stream()
                .map(file -> new PendingUpload(file, s3Service.newKey()))
                .toList();
        List<Long> fileIds = transactionTemplate.execute(status ->
                persistPendingFiles(userId, uploadsId, folderId, pending));
        try {
            for (PendingUpload p : pending) {
                s3Service.upload(bucket, p.key(), p.file());
            }
            transactionTemplate.executeWithoutResult(status -> completeBatch(uploadsId, fileIds));
            return new UploadResponse(uploadsId, fileIds);
        } catch (RuntimeException e) {
            for (PendingUpload p : pending) {
                deleteQuietly(p.key());
            }
            transactionTemplate.executeWithoutResult(status -> failUpload(uploadsId, fileIds));
            throw e;
        }
    }

    @Transactional(readOnly = true)
    public UploadStatusResponse getStatus(Long userId, Long uploadsId) {
        Uploads uploads = uploadsRepository.findById(uploadsId)
                .orElseThrow(() -> new FileNotFoundException("업로드 배치를 찾을 수 없습니다: " + uploadsId));
        if (!uploads.getUsersId().equals(userId)) {
            throw new ForbiddenException("해당 업로드에 접근할 권한이 없습니다.");
        }
        return new UploadStatusResponse(
                uploads.getStatus(),
                uploads.getTotal(),
                uploads.getCreatedAt(),
                uploads.getFinishedAt());
    }

    private Uploads findExistingBatch(Long userId, String idempotencyKey) {
        if (idempotencyKey == null || idempotencyKey.isBlank()) {
            return null;
        }
        return uploadsRepository.findByUsersIdAndIdempotencyKey(userId, idempotencyKey).orElse(null);
    }

    private Long createBatch(Long userId, int total, String idempotencyKey) {
        Uploads uploads = new Uploads();
        uploads.setUsersId(userId);
        uploads.setStatus(UploadStatus.UPLOADING);
        uploads.setTotal(total);
        uploads.setIdempotencyKey(idempotencyKey);
        uploadsRepository.save(uploads);
        return uploads.getId();
    }

    private List<Long> persistPendingFiles(Long userId, Long uploadsId, Long folderId, List<PendingUpload> pending) {
        List<Long> fileIds = new ArrayList<>();
        for (PendingUpload p : pending) {
            File entity = new File();
            entity.setUsersId(userId);
            entity.setFolderId(folderId);
            entity.setUploadsId(uploadsId);
            entity.setName(p.file().getOriginalFilename());
            entity.setS3Key(p.key());
            entity.setFileType(extensionOf(p.file().getOriginalFilename()));
            entity.setSizeBytes(p.file().getSize());
            fileRepository.save(entity);
            fileIds.add(entity.getId());
        }
        return fileIds;
    }

    private void completeBatch(Long uploadsId, List<Long> fileIds) {
        for (Long fileId : fileIds) {
            jobService.enqueueIngest(fileId, uploadsId);
        }
        uploadsRepository.findById(uploadsId).ifPresent(uploads -> {
            uploads.setStatus(UploadStatus.COMPLETED);
            uploads.setFinishedAt(LocalDateTime.now());
        });
    }

    private void failUpload(Long uploadsId, List<Long> fileIds) {
        fileRepository.deleteAllById(fileIds);
        markFailed(uploadsId);
    }

    private void markFailed(Long uploadsId) {
        uploadsRepository.findById(uploadsId).ifPresent(uploads -> {
            uploads.setStatus(UploadStatus.FAILED);
            uploads.setFinishedAt(LocalDateTime.now());
        });
    }

    private void deleteQuietly(String key) {
        try {
            s3Service.delete(bucket, key);
        } catch (RuntimeException e) {
            log.warn("S3 보상 삭제 실패 - 고아 객체 가능(key={}): {}", key, e.toString());
        }
    }

    private void validate(List<MultipartFile> files) {
        if (files == null || files.isEmpty()) {
            throw new UploadLimitExceededException("업로드할 파일이 없습니다.");
        }
        if (files.size() > MAX_FILE_COUNT) {
            throw new UploadLimitExceededException(
                    "한 번에 최대 " + MAX_FILE_COUNT + "개까지 업로드할 수 있습니다.");
        }
        for (MultipartFile file : files) {
            if (file.isEmpty()) {
                throw new UploadLimitExceededException("빈 파일은 업로드할 수 없습니다.");
            }
            if (file.getSize() > MAX_FILE_SIZE) {
                throw new UploadLimitExceededException(
                        "파일당 최대 20MB까지 업로드할 수 있습니다: " + file.getOriginalFilename());
            }
            String ext = extensionOf(file.getOriginalFilename());
            if (!ALLOWED_EXTENSIONS.contains(ext)) {
                throw new UnsupportedFileTypeException(
                        "지원하지 않는 파일 형식입니다: " + file.getOriginalFilename());
            }
        }
    }

    private String extensionOf(String filename) {
        if (filename == null) {
            return "";
        }
        int dot = filename.lastIndexOf('.');
        if (dot < 0 || dot == filename.length() - 1) {
            return "";
        }
        return filename.substring(dot + 1).toLowerCase();
    }

    private record PendingUpload(MultipartFile file, String key) {
    }
}