package com.jipsa.internal;

import com.jipsa.common.exception.FileNotFoundException;
import com.jipsa.file.File;
import com.jipsa.file.FileRepository;
import com.jipsa.file.S3Service;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;

@Service
public class IngestManifestService {

    private final FileRepository fileRepository;
    private final S3Service s3Service;
    private final String bucket;
    private final Duration presignTtl;

    public IngestManifestService(FileRepository fileRepository,
                                 S3Service s3Service,
                                 @Value("${app.s3.bucket}") String bucket,
                                 @Value("${app.internal.presign-ttl-seconds:900}") long presignTtlSeconds) {
        this.fileRepository = fileRepository;
        this.s3Service = s3Service;
        this.bucket = bucket;
        this.presignTtl = Duration.ofSeconds(presignTtlSeconds);
    }

    @Transactional(readOnly = true)
    public IngestManifest build(Long fileIdx) {
        File file = fileRepository.findByIdAndDeletedAtIsNull(fileIdx)
                .orElseThrow(() -> new FileNotFoundException("파일을 찾을 수 없습니다: " + fileIdx));
        return build(file);
    }

    public IngestManifest build(File file) {
        return new IngestManifest(
                file.getId(),
                file.getUsersId(),
                file.getFolderId(),
                file.getName(),
                file.getFileType(),
                s3Service.presignedGetUrl(bucket, file.getS3Key(), presignTtl),
                presignTtl.toSeconds());
    }
}