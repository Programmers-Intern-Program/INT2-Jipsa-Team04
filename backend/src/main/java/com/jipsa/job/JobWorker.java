package com.jipsa.job;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.net.InetAddress;
import java.net.UnknownHostException;
import java.time.Duration;
import java.util.List;
import java.util.UUID;

@Component
@ConditionalOnProperty(name = "app.ingest.enabled", havingValue = "true", matchIfMissing = true)
public class JobWorker {

    private static final Logger log = LoggerFactory.getLogger(JobWorker.class);

    private final JobService jobService;
    private final JobProcessingService processingService;
    private final int batchSize;
    private final long ownershipTtlMs;
    private final String workerId;

    public JobWorker(JobService jobService,
                     JobProcessingService processingService,
                     @Value("${app.ingest.batch-size:5}") int batchSize,
                     @Value("${app.ingest.ownership-ttl-ms:60000}") long ownershipTtlMs) {
        this.jobService = jobService;
        this.processingService = processingService;
        this.batchSize = batchSize;
        this.ownershipTtlMs = ownershipTtlMs;
        this.workerId = buildWorkerId();
    }

    @Scheduled(fixedDelayString = "${app.ingest.poll-interval-ms:2000}")
    public void poll() {
        List<Long> claimed = jobService.claimBatch(workerId, batchSize, Duration.ofMillis(ownershipTtlMs));
        for (Long jobId : claimed) {
            try {
                processingService.process(jobId, workerId);
            } catch (RuntimeException e) {
                log.error("Unexpected error processing job {}", jobId, e);
            }
        }
    }

    @Scheduled(fixedDelayString = "${app.ingest.reap-interval-ms:30000}")
    public void reap() {
        try {
            processingService.reapExpiredExhaustedJobs();
        } catch (RuntimeException e) {
            log.error("Unexpected error reaping stuck jobs", e);
        }
    }

    private static String buildWorkerId() {
        String host;
        try {
            host = InetAddress.getLocalHost().getHostName();
        } catch (UnknownHostException e) {
            host = "unknown";
        }
        return host + "-" + UUID.randomUUID();
    }
}