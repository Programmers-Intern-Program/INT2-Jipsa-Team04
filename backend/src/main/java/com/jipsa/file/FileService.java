package com.jipsa.file;

import com.jipsa.common.exception.FileNotFoundException;
import com.jipsa.common.exception.ForbiddenException;
import com.jipsa.job.Job;
import com.jipsa.job.JobRepository;
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

    public FileService(FileRepository fileRepository, JobRepository jobRepository) {
        this.fileRepository = fileRepository;
        this.jobRepository = jobRepository;
    }

    @Transactional(readOnly = true)
    public FileListResponse list(Long userId, Long folderId, String keyword, String docType, int page) {
        Pageable pageable = PageRequest.of(page, PAGE_SIZE, Sort.by(Sort.Direction.DESC, "createdAt"));
        Page<File> result = fileRepository.search(userId, folderId, keyword, docType, pageable);
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
}