package com.jipsa.job;

import com.jipsa.file.File;
import com.jipsa.file.FileRepository;
import com.jipsa.file.FileStatus;
import com.jipsa.file.FileMetadata;
import com.jipsa.file.FileMetadataRepository;
import com.jipsa.internal.IngestManifestService;
import com.jipsa.internal.IngestManifest;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

import java.time.Duration;
import java.time.LocalDateTime;

@Service
public class JobProcessingService {

    private static final Logger log = LoggerFactory.getLogger(JobProcessingService.class);

    private final JobRepository jobRepository;
    private final FileRepository fileRepository;
    private final FileMetadataRepository fileMetadataRepository;
    private final IngestManifestService ingestManifestService;
    private final RagIngestClient ragIngestClient;
    private final TransactionTemplate transactionTemplate;
    private final long retryBackoffMs;
    private final long callbackTimeoutMs;

    public JobProcessingService(JobRepository jobRepository,
                                FileRepository fileRepository,
                                FileMetadataRepository fileMetadataRepository,
                                IngestManifestService ingestManifestService,
                                RagIngestClient ragIngestClient,
                                PlatformTransactionManager transactionManager,
                                @Value("${app.ingest.retry-backoff-ms:5000}") long retryBackoffMs,
                                @Value("${app.ingest.callback-timeout-ms:600000}") long callbackTimeoutMs) {
        this.jobRepository = jobRepository;
        this.fileRepository = fileRepository;
        this.fileMetadataRepository = fileMetadataRepository;
        this.ingestManifestService = ingestManifestService;
        this.ragIngestClient = ragIngestClient;
        this.transactionTemplate = new TransactionTemplate(transactionManager);
        this.retryBackoffMs = retryBackoffMs;
        this.callbackTimeoutMs = callbackTimeoutMs;
    }

    public void process(Long jobId, String workerId) {
        HandoffPlan plan = transactionTemplate.execute(status -> prepareHandoff(jobId, workerId));
        if (plan == null || !plan.push()) {
            return;
        }
        try {
            ragIngestClient.push(plan.manifest());
        } catch (RuntimeException e) {
            transactionTemplate.executeWithoutResult(status -> markHandoffFailed(jobId, e));
            return;
        }
        try {
            transactionTemplate.executeWithoutResult(status -> finalizeAfterPush(jobId));
        } catch (RuntimeException e) {
            log.warn("Job {} pushed to RAG but recording handoff state failed; reconciler will recover", jobId, e);
        }
    }

    private HandoffPlan prepareHandoff(Long jobId, String workerId) {
        Job job = jobRepository.findById(jobId).orElse(null);
        if (job == null || job.getJobStatus() != JobStatus.RUNNING) {
            return HandoffPlan.skip();
        }
        if (!workerId.equals(job.getWorkerId())) {
            return HandoffPlan.skip();
        }
        File file = job.getFileId() == null
                ? null
                : fileRepository.findByIdAndDeletedAtIsNull(job.getFileId()).orElse(null);
        if (job.getFileId() != null && file == null) {
            job.setJobStatus(JobStatus.CANCELLED);
            job.setErrorMessage("파일이 삭제되어 인제스트를 취소했습니다.");
            job.setFinishedAt(LocalDateTime.now());
            return HandoffPlan.skip();
        }
        if (file == null) {
            job.setJobStatus(JobStatus.SUCCESS);
            job.setErrorMessage(null);
            job.setFinishedAt(LocalDateTime.now());
            return HandoffPlan.skip();
        }
        if (file.getStatus() == FileStatus.READY) {
            job.setJobStatus(JobStatus.SUCCESS);
            job.setErrorMessage(null);
            job.setFinishedAt(LocalDateTime.now());
            return HandoffPlan.skip();
        }
        file.setStatus(FileStatus.PROCESSING);
        file.setErrorMessage(null);
        file.setProcessingStage(null);
        markMetadataProcessing(file);
        return HandoffPlan.push(ingestManifestService.build(file));
    }

    private void finalizeAfterPush(Long jobId) {
        LocalDateTime now = LocalDateTime.now();
        Job job = jobRepository.findById(jobId).orElse(null);
        if (job == null || job.getJobStatus() != JobStatus.RUNNING) {
            return;
        }
        File file = job.getFileId() == null
                ? null
                : fileRepository.findByIdAndDeletedAtIsNull(job.getFileId()).orElse(null);
        if (file != null && file.getStatus() == FileStatus.READY) {
            job.setJobStatus(JobStatus.SUCCESS);
            job.setErrorMessage(null);
            job.setFinishedAt(now);
            log.info("Job {} completed via synchronous callback (file {})", jobId, job.getFileId());
            return;
        }
        if (file != null && file.getStatus() == FileStatus.FAILED) {
            job.setJobStatus(JobStatus.FAILED);
            job.setFinishedAt(now);
            return;
        }
        job.setJobStatus(JobStatus.WAITING_CALLBACK);
        job.setWorkerId(null);
        job.setOwnershipExpiresAt(now.plus(Duration.ofMillis(callbackTimeoutMs)));
        log.info("Job {} handed off to RAG, awaiting completion callback (file {})", jobId, job.getFileId());
    }

    public void reconcileTimedOutCallbacks() {
        LocalDateTime now = LocalDateTime.now();
        for (Long jobId : jobRepository.findTimedOutWaitingCallbackIds(now)) {
            transactionTemplate.executeWithoutResult(status -> reconcileCallback(jobId));
        }
    }

    private void reconcileCallback(Long jobId) {
        LocalDateTime now = LocalDateTime.now();
        Job job = jobRepository.findById(jobId).orElse(null);
        if (job == null || job.getJobStatus() != JobStatus.WAITING_CALLBACK) {
            return;
        }
        File file = job.getFileId() == null
                ? null
                : fileRepository.findForUpdate(job.getFileId()).orElse(null);
        if (file != null && file.getStatus() == FileStatus.READY) {
            job.setJobStatus(JobStatus.SUCCESS);
            job.setErrorMessage(null);
            job.setFinishedAt(now);
            return;
        }
        if (job.getAttempts() >= job.getMaxAttempts()) {
            job.setJobStatus(JobStatus.FAILED);
            job.setErrorMessage("RAG 완료 콜백 시간 초과 (최대 재시도 초과)");
            job.setFinishedAt(now);
            if (file != null) {
                file.setStatus(FileStatus.FAILED);
                file.setErrorMessage("RAG 완료 콜백을 받지 못했습니다.");
                file.setProcessingStage(null);
                markMetadataFailed(file);
            }
            log.warn("Job {} callback timed out, marked FAILED (file {})", jobId, job.getFileId());
            return;
        }
        job.setJobStatus(JobStatus.RETRY_WAIT);
        job.setErrorMessage("RAG 완료 콜백 시간 초과, 재시도 예정");
        job.setWorkerId(null);
        job.setOwnershipExpiresAt(null);
        job.setNextAttemptAt(now.plus(Duration.ofMillis(retryBackoffMs)));
        log.warn("Job {} callback timed out, scheduling retry (file {})", jobId, job.getFileId());
    }

    private void markHandoffFailed(Long jobId, RuntimeException e) {
        Job job = jobRepository.findById(jobId).orElse(null);
        if (job == null) {
            return;
        }
        File file = job.getFileId() == null
                ? null
                : fileRepository.findByIdAndDeletedAtIsNull(job.getFileId()).orElse(null);
        handleFailure(job, file, e);
    }

    private record HandoffPlan(boolean push, IngestManifest manifest) {
        static HandoffPlan skip() {
            return new HandoffPlan(false, null);
        }
        static HandoffPlan push(IngestManifest manifest) {
            return new HandoffPlan(true, manifest);
        }
    }

    private void markMetadataProcessing(File file) {
        FileMetadata metadata = fileMetadataRepository.findById(file.getId()).orElseGet(() -> {
            FileMetadata created = new FileMetadata();
            created.setFileId(file.getId());
            created.setFileType(file.getFileType());
            return created;
        });
        metadata.setExtractionStatus("PROCESSING");
        fileMetadataRepository.save(metadata);
    }

    private void markMetadataFailed(File file) {
        fileMetadataRepository.findById(file.getId()).ifPresent(metadata -> {
            metadata.setExtractionStatus("FAILED");
            fileMetadataRepository.save(metadata);
        });
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
                markMetadataFailed(file);
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

    @Transactional
    public void reapExpiredExhaustedJobs() {
        LocalDateTime now = LocalDateTime.now();
        for (Long jobId : jobRepository.findExpiredExhaustedIds(now)) {
            Job job = jobRepository.findById(jobId).orElse(null);
            if (job == null
                    || job.getJobStatus() != JobStatus.RUNNING
                    || job.getOwnershipExpiresAt() == null
                    || job.getOwnershipExpiresAt().isAfter(now)
                    || job.getAttempts() < job.getMaxAttempts()) {
                continue;
            }
            String message = "최대 재시도 횟수를 초과한 뒤 소유권이 만료되어 실패 처리했습니다.";
            job.setJobStatus(JobStatus.FAILED);
            job.setErrorMessage(message);
            job.setWorkerId(null);
            job.setOwnershipExpiresAt(null);
            job.setFinishedAt(now);
            if (job.getFileId() != null) {
                fileRepository.findByIdAndDeletedAtIsNull(job.getFileId()).ifPresent(file -> {
                    file.setStatus(FileStatus.FAILED);
                    file.setErrorMessage(message);
                    file.setProcessingStage(null);
                    markMetadataFailed(file);
                });
            }
            log.warn("Reaped stuck job {} (file {}) as FAILED", job.getId(), job.getFileId());
        }
    }
}