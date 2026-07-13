package com.jipsa.file;

import io.awspring.cloud.s3.S3Template;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.util.UUID;

@Service
public class S3Service {

    private final S3Template s3Template;

    public S3Service(S3Template s3Template) {
        this.s3Template = s3Template;
    }

    public String upload(String bucket, MultipartFile file) {
        try {
            String key = "files/" + UUID.randomUUID();
            s3Template.upload(bucket, key, file.getInputStream());
            return key;
        } catch (IOException e) {
            throw new RuntimeException("S3 업로드 실패", e);
        }
    }
}