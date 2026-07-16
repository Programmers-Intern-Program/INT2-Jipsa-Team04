package com.jipsa.file;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.jipsa.common.BadRequestException;
import com.jipsa.common.exception.FileNotFoundException;
import com.jipsa.common.exception.ForbiddenException;
import com.jipsa.folder.FolderNotFoundException;
import com.jipsa.folder.FolderRepository;
import com.jipsa.job.Job;
import com.jipsa.job.JobRepository;
import com.jipsa.job.JobService;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.Sort;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.LocalTime;
import java.util.List;

@Service
public class FileService {

    private static final int PAGE_SIZE = 20;

    private final FileRepository fileRepository;
    private final JobRepository jobRepository;
    private final JobService jobService;
    private final FolderRepository folderRepository;
    private final FileMetadataRepository fileMetadataRepository;
    private final S3Service s3Service;
    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();
    private final String bucket;
    private final long storageQuotaBytes;

    public FileService(FileRepository fileRepository,
                       JobRepository jobRepository,
                       JobService jobService,
                       FolderRepository folderRepository,
                       FileMetadataRepository fileMetadataRepository,
                       S3Service s3Service,
                       @Value("${app.s3.bucket}") String bucket,
                       @Value("${app.storage.quota-bytes:107374182400}") long storageQuotaBytes) {
        this.fileRepository = fileRepository;
        this.jobRepository = jobRepository;
        this.jobService = jobService;
        this.folderRepository = folderRepository;
        this.fileMetadataRepository = fileMetadataRepository;
        this.s3Service = s3Service;
        this.bucket = bucket;
        this.storageQuotaBytes = storageQuotaBytes;
    }

    @Transactional(readOnly = true)
    public FileListResponse list(Long userId, Long folderId, String keyword, String docType,
                                 String tags, LocalDate dateFrom, LocalDate dateTo, int page) {
        Pageable pageable = PageRequest.of(page, PAGE_SIZE, Sort.by(Sort.Direction.DESC, "createdAt"));
        LocalDateTime from = dateFrom == null ? null : dateFrom.atStartOfDay();
        LocalDateTime to = dateTo == null ? null : dateTo.atTime(LocalTime.MAX);
        Page<File> result = fileRepository.search(userId, folderId, escapeLike(keyword), docType,
                blankToNull(tags), from, to, pageable);
        List<FileListItem> items = result.getContent().stream()
                .map(this::toListItem)
                .toList();
        return new FileListResponse(items, result.getTotalElements(), result.getNumber(), result.getSize());
    }

    @Transactional(readOnly = true)
    public FileDetailResponse getDetail(Long userId, Long fileId) {
        File file = requireOwnedFile(userId, fileId);
        FileMetadata metadata = fileMetadataRepository.findById(fileId).orElse(null);
        String summary = metadata != null && metadata.getSummary() != null ? metadata.getSummary() : "";
        List<String> tags = metadata != null ? parseStringList(metadata.getTags()) : List.of();
        return new FileDetailResponse(
                file.getName(),
                file.getFileType(),
                file.getSizeBytes(),
                file.getFolderId(),
                file.getOwnerName(),
                file.isStar(),
                summary,
                tags,
                "",
                new FileDetailResponse.Entities(List.of(), List.of(), List.of(), null),
                file.getUpdatedAt(),
                file.getStatus(),
                file.getProcessingStage(),
                file.getSecurityRank(),
                file.isPiiDetected());
    }

    @Transactional(readOnly = true)
    public FileStatusResponse getStatus(Long userId, Long fileId) {
        File file = requireOwnedFile(userId, fileId);
        Integer attempts = jobRepository.findTopByFileIdOrderByCreatedAtDesc(fileId)
                .map(Job::getAttempts)
                .orElse(0);
        return new FileStatusResponse(
                file.getStatus(),
                file.getProcessingStage(),
                attempts,
                file.getErrorMessage());
    }

    @Transactional
    public void softDelete(Long userId, Long fileId) {
        File file = requireOwnedFile(userId, fileId);
        file.setStatus(FileStatus.DELETED);
        file.setDeletedAt(LocalDateTime.now());
    }

    @Transactional(readOnly = true)
    public FileListResponse listTrash(Long userId, int page) {
        Pageable pageable = PageRequest.of(page, PAGE_SIZE);
        Page<File> result = fileRepository
                .findByUsersIdAndDeletedAtIsNotNullOrderByDeletedAtDesc(userId, pageable);
        List<FileListItem> items = result.getContent().stream()
                .map(this::toListItem)
                .toList();
        return new FileListResponse(items, result.getTotalElements(), result.getNumber(), result.getSize());
    }

    @Transactional
    public void restore(Long userId, Long fileId) {
        File file = fileRepository.findById(fileId)
                .orElseThrow(() -> new FileNotFoundException("파일을 찾을 수 없습니다: " + fileId));
        if (!file.getUsersId().equals(userId)) {
            throw new ForbiddenException("해당 파일에 접근할 권한이 없습니다.");
        }
        if (file.getDeletedAt() == null) {
            throw new BadRequestException("삭제되지 않은 파일입니다: " + fileId);
        }
        file.setDeletedAt(null);
        file.setStatus(FileStatus.UPLOADED);
        file.setProcessingStage(null);
        file.setErrorMessage(null);
        jobService.enqueueIngest(file.getId(), file.getUploadsId());
    }

    @Transactional(readOnly = true)
    public StorageUsageResponse getStorageUsage(Long userId) {
        long used = fileRepository.sumSizeBytesByUsersId(userId);
        return new StorageUsageResponse(used, storageQuotaBytes);
    }

    @Transactional
    public void moveToFolder(Long userId, Long fileId, Long folderId) {
        File file = requireOwnedFile(userId, fileId);
        if (folderId != null) {
            folderRepository.findByIdAndUsersId(folderId, userId)
                    .orElseThrow(() -> new FolderNotFoundException(folderId));
        }
        file.setFolderId(folderId);
    }

    @Transactional
    public void moveFilesToFolder(Long userId, List<Long> fileIds, Long folderId) {
        if (fileIds == null || fileIds.isEmpty()) {
            throw new BadRequestException("이동할 파일이 없습니다.");
        }
        List<File> files = fileIds.stream()
                .map(fileId -> requireOwnedFile(userId, fileId))
                .toList();
        if (folderId != null) {
            folderRepository.findByIdAndUsersId(folderId, userId)
                    .orElseThrow(() -> new FolderNotFoundException(folderId));
        }
        files.forEach(file -> file.setFolderId(folderId));
    }

    @Transactional
    public void setStar(Long userId, Long fileId, boolean star) {
        File file = requireOwnedFile(userId, fileId);
        file.setStar(star);
    }

    @Transactional
    public void rename(Long userId, Long fileId, String name) {
        if (name == null || name.isBlank()) {
            throw new BadRequestException("파일명은 비어 있을 수 없습니다.");
        }
        File file = requireOwnedFile(userId, fileId);
        file.setName(name.trim());
    }

    public FileDownload download(Long userId, Long fileId) {
        File file = requireOwnedFile(userId, fileId);
        S3Service.Content content = s3Service.download(bucket, file.getS3Key());
        String contentType = content.contentType() != null ? content.contentType() : "application/octet-stream";
        return new FileDownload(content.resource(), file.getName(), contentType, content.contentLength());
    }

    private File requireOwnedFile(Long userId, Long fileId) {
        File file = fileRepository.findByIdAndDeletedAtIsNull(fileId)
                .orElseThrow(() -> new FileNotFoundException("파일을 찾을 수 없습니다: " + fileId));
        if (!file.getUsersId().equals(userId)) {
            throw new ForbiddenException("해당 파일에 접근할 권한이 없습니다.");
        }
        return file;
    }

    private FileListItem toListItem(File file) {
        return new FileListItem(
                file.getId(),
                file.getName(),
                file.getFileType(),
                file.getSizeBytes(),
                file.getFolderId(),
                file.getStatus(),
                file.isStar(),
                file.getUpdatedAt());
    }

    private String escapeLike(String keyword) {
        if (keyword == null) {
            return null;
        }
        return keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_");
    }

    private List<String> parseStringList(String json) {
        if (json == null || json.isBlank()) {
            return List.of();
        }
        try {
            List<String> parsed = OBJECT_MAPPER.readValue(json, new TypeReference<List<String>>() {});
            return parsed == null ? List.of() : parsed;
        } catch (JsonProcessingException e) {
            return List.of();
        }
    }

    private String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value;
    }
}