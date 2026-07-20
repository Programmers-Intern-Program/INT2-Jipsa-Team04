package com.jipsa.job;

import com.jipsa.file.File;
import com.jipsa.file.FileRepository;
import com.jipsa.file.FileStatus;
import com.jipsa.internal.IngestManifestService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;
import java.time.LocalDateTime;

@Service
public class JobProcessingService {

    private static final Logger log = LoggerFactory.getLogger(JobProcessingService.class);

    private final JobRepository jobRepository;
    private final FileRepository fileRepository;
    private final IngestManifestService ingestManifestService;
    private final RagIngestClient ragIngestClient;
    private final long retryBackoffMs;

    public JobProcessingService(JobRepository jobRepository,
                                FileRepository fileRepository,
                                IngestManifestService ingestManifestService,
                                RagIngestClient ragIngestClient,
                                @Value("${app.ingest.retry-backoff-ms:5000}") long retryBackoffMs) {
        this.jobRepository = jobRepository;
        this.fileRepository = fileRepository;
        this.ingestManifestService = ingestManifestService;
        this.ragIngestClient = ragIngestClient;
        this.retryBackoffMs = retryBackoffMs;
    }

    @Transactional
    public void process(Long jobId) {
        Job job = jobRepository.findById(jobId).orElse(null);
        if (job == null || job.getJobStatus() != JobStatus.RUNNING) {
            return;
        }
        File file = job.getFileId() == null
                ? null
                : fileRepository.findById(job.getFileId()).orElse(null);
        try {
            if (file != null) {
                file.setStatus(FileStatus.PROCESSING);
                file.setErrorMessage(null);
                file.setProcessingStage(null);
                ragIngestClient.push(ingestManifestService.build(file));
            }
            job.setJobStatus(JobStatus.SUCCESS);
            job.setErrorMessage(null);
            job.setFinishedAt(LocalDateTime.now());
            log.info("Job {} handed off to RAG (file {})", jobId, job.getFileId());
        } catch (RuntimeException e) {
            handleFailure(job, file, e);
        }
    }

    private void handleFailure(Job job, File file, RuntimeException e) {
        LocalDateTime now = LocalDateTime.now();
        String message = e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage();
        if (job.getAttempts() >= job.getMaxAttempts()) {
            job.setJobStatus(JobStatus.FAILED);
            job.setErrorMessage(message);
            job.setFinishedAt(now);
            if (file != null) {
                file.setStatus(FileStatus.FAILED);
                file.setErrorMessage(message);
            }
            log.warn("Job {} failed permanently after {} attempts: {}",
                    job.getId(), job.getAttempts(), message);
        } else {
            job.setJobStatus(JobStatus.RETRY_WAIT);
            job.setErrorMessage(message);
            job.setWorkerId(null);
            job.setOwnershipExpiresAt(null);
            job.setNextAttemptAt(now.plus(Duration.ofMillis(retryBackoffMs * job.getAttempts())));
            if (file != null) {
                file.setProcessingStage(null);
            }
            log.warn("Job {} attempt {} failed, scheduling retry: {}",
                    job.getId(), job.getAttempts(), message);
        }
    }
}