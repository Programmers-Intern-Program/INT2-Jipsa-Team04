package com.jipsa.purge;

import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
public class S3DeleteWorker {

    private final S3DeleteService s3DeleteService;

    public S3DeleteWorker(S3DeleteService s3DeleteService) {
        this.s3DeleteService = s3DeleteService;
    }

    @Scheduled(fixedDelayString = "${app.s3.delete.poll-interval-ms:30000}")
    public void drain() {
        s3DeleteService.drainOnce();
    }
}
