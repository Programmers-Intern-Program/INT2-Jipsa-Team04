package com.jipsa.upload;

import com.jipsa.common.exception.FileNotFoundException;
import com.jipsa.common.exception.ForbiddenException;
import com.jipsa.common.exception.UnsupportedFileTypeException;
import com.jipsa.common.exception.UploadLimitExceededException;
import com.jipsa.file.File;
import com.jipsa.file.FileRepository;
import com.jipsa.file.S3Service;
import com.jipsa.job.JobService;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
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

    private final UploadsRepository uploadsRepository;
    private final FileRepository fileRepository;
    private final S3Service s3Service;
    private final JobService jobService;
    private final String bucket;

    public UploadService(UploadsRepository uploadsRepository,
                         FileRepository fileRepository,
                         S3Service s3Service,
                         JobService jobService,
                         @Value("${app.s3.bucket}") String bucket) {
        this.uploadsRepository = uploadsRepository;
        this.fileRepository = fileRepository;
        this.s3Service = s3Service;
        this.jobService = jobService;
        this.bucket = bucket;
    }

    @Transactional
    public UploadResponse upload(Long userId, List<MultipartFile> files) {
        validate(files);

        Uploads uploads = new Uploads();
        uploads.setUsersId(userId);
        uploads.setStatus(UploadStatus.UPLOADING);
        uploads.setTotal(files.size());
        uploadsRepository.save(uploads);

        List<Long> fileIds = new ArrayList<>();
        for (MultipartFile file : files) {
            String key = s3Service.upload(bucket, file);

            File entity = new File();
            entity.setUsersId(userId);
            entity.setFolderId(null);
            entity.setUploadsId(uploads.getId());
            entity.setName(file.getOriginalFilename());
            entity.setS3Key(key);
            entity.setFileType(extensionOf(file.getOriginalFilename()));
            entity.setSizeBytes(file.getSize());
            fileRepository.save(entity);

            jobService.enqueueIngest(entity.getId(), uploads.getId());
            fileIds.add(entity.getId());
        }

        uploads.setStatus(UploadStatus.COMPLETED);
        uploads.setFinishedAt(LocalDateTime.now());

        return new UploadResponse(uploads.getId(), fileIds);
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
}