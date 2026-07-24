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
import com.jipsa.chunk.ChunkRepository;
import com.jipsa.purge.RagPurgeService;
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
import java.util.Map;
import java.util.stream.Collectors;

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
    private final ChunkRepository chunkRepository;
    private final RagPurgeService ragPurgeService;

    public FileService(FileRepository fileRepository,
                       JobRepository jobRepository,
                       JobService jobService,
                       FolderRepository folderRepository,
                       FileMetadataRepository fileMetadataRepository,
                       S3Service s3Service,
                       @Value("${app.s3.bucket}") String bucket,
                       @Value("${app.storage.quota-bytes:107374182400}") long storageQuotaBytes,
                       ChunkRepository chunkRepository,
                       RagPurgeService ragPurgeService) {
        this.fileRepository = fileRepository;
        this.jobRepository = jobRepository;
        this.jobService = jobService;
        this.folderRepository = folderRepository;
        this.fileMetadataRepository = fileMetadataRepository;
        this.s3Service = s3Service;
        this.bucket = bucket;
        this.storageQuotaBytes = storageQuotaBytes;
        this.chunkRepository = chunkRepository;
        this.ragPurgeService = ragPurgeService;
    }

    @Transactional(readOnly = true)
    public FileListResponse list(Long userId, Long folderId, String keyword, String docType,
                                 String tags, LocalDate dateFrom, LocalDate dateTo, String documentType, int page) {
        Pageable pageable = PageRequest.of(page, PAGE_SIZE, Sort.by(Sort.Direction.DESC, "createdAt"));
        LocalDateTime from = dateFrom == null ? null : dateFrom.atStartOfDay();
        LocalDateTime to = dateTo == null ? null : dateTo.atTime(LocalTime.MAX);
        Page<File> result = fileRepository.search(userId, folderId, escapeLike(keyword), docType,
                blankToNull(tags), from, to, blankToNull(documentType), pageable);
        List<FileListItem> items = toListItems(result.getContent());
        return new FileListResponse(items, result.getTotalElements(), result.getNumber(), result.getSize());
    }

    @Transactional(readOnly = true)
    public FileDetailResponse getDetail(Long userId, Long fileId) {
        File file = requireOwnedFile(userId, fileId);
        FileMetadata metadata = fileMetadataRepository.findById(fileId).orElse(null);
        String summary = metadata != null && metadata.getSummary() != null ? metadata.getSummary() : "";
        List<String> tags = metadata != null ? parseStringList(metadata.getTags()) : List.of();
        List<String> keywords = metadata != null ? parseStringList(metadata.getKeywords()) : List.of();
        String documentType = metadata != null ? metadata.getDocumentType() : null;
        String extractionStatus = metadata != null ? metadata.getExtractionStatus() : null;
        Double extractionConfidence = metadata != null ? metadata.getExtractionConfidence() : null;
        return new FileDetailResponse(
                file.getName(),
                file.getFileType(),
                file.getSizeBytes(),
                file.getFolderId(),
                file.getOwnerName(),
                file.isStar(),
                summary,
                tags,
                keywords,
                parseEntities(metadata != null ? metadata.getExtractedEntities() : null),
                file.getUpdatedAt(),
                file.getStatus(),
                file.getProcessingStage(),
                file.getSecurityRank(),
                file.isPiiDetected(),
                documentType,
                extractionStatus,
                extractionConfidence);
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

    /**
     * 폴더 소프트 삭제 시 그 안의 활성 파일들을 함께 휴지통으로 보낸다(FolderService에서 호출).
     * deletedAt은 호출부(FolderService.delete())가 폴더들에 찍은 것과 동일한 값을 넘겨받는다 —
     * "이 폴더 삭제로 같이 삭제된 파일"과 "원래 따로 삭제돼 있던 파일"을 나중에 복원 시 구분하기 위함
     * (restoreByFolderIds 참고).
     */
    @Transactional
    public void softDeleteByFolderIds(List<Long> folderIds, LocalDateTime deletedAt) {
        fileRepository.findByFolderIdInAndDeletedAtIsNull(folderIds)
                .forEach(file -> {
                    file.setStatus(FileStatus.DELETED);
                    file.setDeletedAt(deletedAt);
                });
    }

    /**
     * 폴더 복원 시 그 안의 파일들을 함께 복원한다(FolderService에서 호출).
     * deletedAt이 정확히 일치하는 파일만 복원 대상으로 삼는다 — 폴더가 삭제되기 전에 이미
     * 별도로 휴지통에 들어가 있던 파일까지 폴더 복원에 딸려서 되살아나는 걸 막기 위함.
     */
    @Transactional
    public void restoreByFolderIds(List<Long> folderIds, LocalDateTime deletedAt) {
        fileRepository.findByFolderIdInAndDeletedAt(folderIds, deletedAt)
                .forEach(file -> {
                    file.setDeletedAt(null);
                    file.setProcessingStage(null);
                    file.setErrorMessage(null);
                    if (chunkRepository.countByFileId(file.getId()) > 0) {
                        file.setStatus(FileStatus.READY);
                    } else {
                        file.setStatus(FileStatus.UPLOADED);
                        jobService.enqueueIngest(file.getId(), file.getUploadsId());
                    }
                });
    }

    /** 폴더 영구 삭제 시 그 안의 삭제된 파일들을 S3 실물까지 함께 정리한다(FolderService에서 호출). */
    @Transactional
    public void permanentDeleteByFolderIds(List<Long> folderIds) {
        List<File> files = fileRepository.findByFolderIdInAndDeletedAtIsNotNull(folderIds);
        files.forEach(file -> {
            if (file.getS3Key() != null && !file.getS3Key().isBlank()) {
                s3Service.delete(bucket, file.getS3Key());
            }
            jobRepository.deleteByFileId(file.getId());
            fileMetadataRepository.findById(file.getId()).ifPresent(fileMetadataRepository::delete);
            chunkRepository.deleteByFileId(file.getId());
        });
        fileRepository.deleteAll(files);
        files.forEach(file -> ragPurgeService.enqueue(file.getId(), file.getUsersId()));
    }

    @Transactional(readOnly = true)
    public FileListResponse listTrash(Long userId, int page) {
        Pageable pageable = PageRequest.of(page, PAGE_SIZE);
        Page<File> result = fileRepository
                .findByUsersIdAndDeletedAtIsNotNullOrderByDeletedAtDesc(userId, pageable);
        List<FileListItem> items = toListItems(result.getContent());
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
        // 파일이 속한 폴더가 여전히 삭제(휴지통) 상태면 그 폴더 밑에 그대로 둘 수 없다 —
        // 나중에 그 폴더가 영구삭제될 때 이 파일의 Folder_IDX가 참조 무결성을 깨는 걸 막기 위해
        // 루트로 꺼내놓는다. 사용자가 폴더까지 되돌리고 싶으면 폴더 복원을 별도로 하면 된다.
        if (file.getFolderId() != null && isFolderDeletedOrMissing(file.getFolderId())) {
            file.setFolderId(null);
        }
        file.setDeletedAt(null);
        file.setProcessingStage(null);
        file.setErrorMessage(null);
        if (chunkRepository.countByFileId(file.getId()) > 0) {
            file.setStatus(FileStatus.READY);
        } else {
            file.setStatus(FileStatus.UPLOADED);
            jobService.enqueueIngest(file.getId(), file.getUploadsId());
        }
    }

    private boolean isFolderDeletedOrMissing(Long folderId) {
        return folderRepository.findById(folderId)
                .map(folder -> folder.getDeletedAt() != null)
                .orElse(true);
    }

    @Transactional
    public void permanentDelete(Long userId, Long fileId) {
        File file = fileRepository.findById(fileId)
                .orElseThrow(() -> new FileNotFoundException("파일을 찾을 수 없습니다: " + fileId));
        if (!file.getUsersId().equals(userId)) {
            throw new ForbiddenException("해당 파일에 접근할 권한이 없습니다.");
        }
        if (file.getDeletedAt() == null) {
            throw new BadRequestException("휴지통에 있는 파일만 영구 삭제할 수 있습니다.");
        }
        if (file.getS3Key() != null && !file.getS3Key().isBlank()) {
            s3Service.delete(bucket, file.getS3Key());
        }
        jobRepository.deleteByFileId(fileId);
        fileMetadataRepository.findById(fileId).ifPresent(fileMetadataRepository::delete);
        chunkRepository.deleteByFileId(fileId);
        fileRepository.delete(file);
        ragPurgeService.enqueue(file.getId(), file.getUsersId());
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
        String finalName = applyOriginalExtension(name.trim(), file.getFileType());
        if (finalName.length() > 255) {
            throw new BadRequestException("파일명이 너무 깁니다.");
        }
        file.setName(finalName);
        jobService.enqueueIngest(file.getId(), file.getUploadsId());
    }

    @Transactional
    public void setDocumentType(Long userId, Long fileId, String documentType) {
        File file = requireOwnedFile(userId, fileId);
        String value = documentType == null || documentType.isBlank() ? null : documentType.trim();
        FileMetadata metadata = fileMetadataRepository.findById(fileId).orElseGet(() -> {
            FileMetadata created = new FileMetadata();
            created.setFileId(file.getId());
            created.setFileType(file.getFileType());
            return created;
        });
        metadata.setDocumentType(value);
        fileMetadataRepository.save(metadata);
    }

    @Transactional
    public void setTags(Long userId, Long fileId, List<String> tags) {
        File file = requireOwnedFile(userId, fileId);
        List<String> normalized = normalizeTags(tags);
        FileMetadata metadata = fileMetadataRepository.findById(fileId).orElseGet(() -> {
            FileMetadata created = new FileMetadata();
            created.setFileId(file.getId());
            created.setFileType(file.getFileType());
            return created;
        });
        metadata.setTags(writeStringList(normalized));
        fileMetadataRepository.save(metadata);
    }

    private String applyOriginalExtension(String requestedName, String originalFileType) {
        if (originalFileType == null || originalFileType.isBlank()) {
            return requestedName;
        }
        String base = requestedName;
        int dot = requestedName.lastIndexOf('.');
        if (dot > 0) {
            base = requestedName.substring(0, dot);
        }
        return base + "." + originalFileType.toLowerCase();
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

    private List<FileListItem> toListItems(List<File> files) {
        Map<Long, FileMetadata> metaById = fileMetadataRepository
                .findAllById(files.stream().map(File::getId).toList())
                .stream()
                .collect(Collectors.toMap(FileMetadata::getFileId, m -> m));
        return files.stream()
                .map(file -> toListItem(file, metaById.get(file.getId())))
                .toList();
    }

    private FileListItem toListItem(File file, FileMetadata metadata) {
        String summary = metadata != null && metadata.getSummary() != null ? metadata.getSummary() : "";
        List<String> tags = metadata != null ? parseStringList(metadata.getTags()) : List.of();
        return new FileListItem(
                file.getId(),
                file.getName(),
                file.getFileType(),
                file.getSizeBytes(),
                file.getFolderId(),
                file.getStatus(),
                file.isStar(),
                file.getUpdatedAt(),
                summary,
                tags,
                file.getSecurityRank(),
                metadata != null ? metadata.getDocumentType() : null);
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

    private FileDetailResponse.Entities parseEntities(String json) {
        if (json == null || json.isBlank()) {
            return emptyEntities();
        }
        try {
            FileDetailResponse.Entities parsed = OBJECT_MAPPER.readValue(json, FileDetailResponse.Entities.class);
            return new FileDetailResponse.Entities(
                    parsed.dates() == null ? List.of() : parsed.dates(),
                    parsed.people() == null ? List.of() : parsed.people(),
                    parsed.amounts() == null ? List.of() : parsed.amounts(),
                    parsed.project());
        } catch (JsonProcessingException e) {
            return emptyEntities();
        }
    }

    private FileDetailResponse.Entities emptyEntities() {
        return new FileDetailResponse.Entities(List.of(), List.of(), List.of(), null);
    }

    private List<String> normalizeTags(List<String> tags) {
        if (tags == null) {
            return List.of();
        }
        return tags.stream()
                .filter(tag -> tag != null)
                .map(String::trim)
                .filter(tag -> !tag.isEmpty() && tag.length() <= 50)
                .distinct()
                .limit(30)
                .toList();
    }

    private String writeStringList(List<String> values) {
        try {
            return OBJECT_MAPPER.writeValueAsString(values);
        } catch (JsonProcessingException e) {
            return "[]";
        }
    }

    private String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value;
    }
}