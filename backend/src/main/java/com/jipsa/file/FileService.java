package com.jipsa.file;

import com.jipsa.common.BadRequestException;
import com.jipsa.common.exception.FileNotFoundException;
import com.jipsa.common.exception.ForbiddenException;
import com.jipsa.folder.FolderNotFoundException;
import com.jipsa.folder.FolderRepository;
import com.jipsa.job.Job;
import com.jipsa.job.JobRepository;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.Sort;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;

@Service
public class FileService {

    private static final int PAGE_SIZE = 20;

    private final FileRepository fileRepository;
    private final JobRepository jobRepository;
    private final FolderRepository folderRepository;
    private final S3Service s3Service;
    private final String bucket;

    public FileService(FileRepository fileRepository,
                       JobRepository jobRepository,
                       FolderRepository folderRepository,
                       S3Service s3Service,
                       @Value("${app.s3.bucket}") String bucket) {
        this.fileRepository = fileRepository;
        this.jobRepository = jobRepository;
        this.folderRepository = folderRepository;
        this.s3Service = s3Service;
        this.bucket = bucket;
    }

    @Transactional(readOnly = true)
    public FileListResponse list(Long userId, Long folderId, String keyword, String docType, int page) {
        Pageable pageable = PageRequest.of(page, PAGE_SIZE, Sort.by(Sort.Direction.DESC, "createdAt"));
        Page<File> result = fileRepository.search(userId, folderId, escapeLike(keyword), docType, pageable);
        List<FileListItem> items = result.getContent().stream()
                .map(this::toListItem)
                .toList();
        return new FileListResponse(items, result.getTotalElements());
    }

    @Transactional(readOnly = true)
    public FileDetailResponse getDetail(Long userId, Long fileId) {
        File file = requireOwnedFile(userId, fileId);
        return new FileDetailResponse(
                file.getName(),
                file.getFileType(),
                file.getSizeBytes(),
                file.getFolderId(),
                file.getOwnerName(),
                file.isStar(),
                null,
                null,
                null,
                null,
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
}