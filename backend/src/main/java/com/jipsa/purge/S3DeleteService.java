package com.jipsa.purge;

import com.jipsa.file.S3Service;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import software.amazon.awssdk.core.exception.SdkClientException;
import software.amazon.awssdk.services.s3.model.S3Exception;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

@Service
public class S3DeleteService {

    private static final String PENDING = "PENDING";
    private static final String PROCESSING = "PROCESSING";
    private static final String DONE = "DONE";
    private static final String FAILED = "FAILED";
    private static final long MAX_RETRY_DELAY_MS = Duration.ofHours(1).toMillis();

    private final S3DeleteTaskRepository taskRepository;
    private final S3Service s3Service;
    private final String bucket;
    private final long retryBackoffMs;
    private final int maxAttempts;
    private final long claimLeaseMs;
    private final TransactionTemplate transactionTemplate;

    public S3DeleteService(S3DeleteTaskRepository taskRepository,
                           S3Service s3Service,
                           @Value("${app.s3.bucket}") String bucket,
                           @Value("${app.s3.delete.retry-backoff-ms:60000}") long retryBackoffMs,
                           @Value("${app.s3.delete.max-attempts:5}") int maxAttempts,
                           @Value("${app.s3.delete.claim-lease-ms:300000}") long claimLeaseMs,
                           @Value("${app.s3.delete.operation-timeout-ms:120000}") long operationTimeoutMs,
                           PlatformTransactionManager transactionManager) {
        this.taskRepository = taskRepository;
        this.s3Service = s3Service;
        this.bucket = bucket;
        this.retryBackoffMs = Math.max(1L, retryBackoffMs);
        this.maxAttempts = Math.max(1, maxAttempts);
        this.claimLeaseMs = Math.max(Math.max(1L, claimLeaseMs), Math.max(1L, operationTimeoutMs) + 30_000L);
        this.transactionTemplate = new TransactionTemplate(transactionManager);
    }

    public void enqueue(Long fileId, Long usersId, String s3Key) {
        if (s3Key == null || s3Key.isBlank()) {
            return;
        }
        S3DeleteTask task = new S3DeleteTask();
        task.setFileId(fileId);
        task.setUsersId(usersId);
        task.setBucket(bucket);
        task.setS3Key(s3Key);
        task.setNextAttemptAt(LocalDateTime.now());
        taskRepository.saveAndFlush(task);
    }

    public void drainOnce() {
        LocalDateTime now = LocalDateTime.now();
        try {
            transactionTemplate.executeWithoutResult(status -> recoverExpiredTasks(now));
        } catch (RuntimeException ignored) {
            return;
        }

        List<S3DeleteTask> tasks;
        try {
            tasks = taskRepository.findTop50ByStatusAndNextAttemptAtBeforeOrderByNextAttemptAt(PENDING, now);
        } catch (RuntimeException ignored) {
            return;
        }

        for (S3DeleteTask task : tasks) {
            process(task.getId());
        }
    }

    private void recoverExpiredTasks(LocalDateTime now) {
        taskRepository.failExpiredClaims(
                PROCESSING,
                FAILED,
                now,
                maxAttempts,
                "S3 삭제 작업 선점이 만료되었고 최대 재시도 횟수를 초과했습니다.");
        taskRepository.requeueExpiredClaims(
                PROCESSING,
                PENDING,
                now,
                maxAttempts,
                "S3 삭제 작업 선점이 만료되어 재시도합니다.");
        taskRepository.failExhaustedPending(
                PENDING,
                FAILED,
                maxAttempts,
                "S3 삭제 최대 재시도 횟수를 초과했습니다.");
    }

    private void process(Long taskId) {
        Optional<ClaimedTask> claimed;
        try {
            claimed = transactionTemplate.execute(status -> claim(taskId, LocalDateTime.now()));
        } catch (RuntimeException ignored) {
            return;
        }
        if (claimed == null || claimed.isEmpty()) {
            return;
        }

        ClaimedTask task = claimed.get();
        try {
            s3Service.delete(task.bucket(), task.s3Key());
            markDone(task);
        } catch (S3Exception e) {
            int status = e.statusCode();
            if (status == 404) {
                markDone(task);
                return;
            }
            handleFailure(task, isRetryableStatus(status), formatError(status, e));
        } catch (SdkClientException e) {
            handleFailure(task, true, formatError(e));
        } catch (RuntimeException e) {
            handleFailure(task, true, formatError(e));
        }
    }

    private Optional<ClaimedTask> claim(Long taskId, LocalDateTime now) {
        LocalDateTime leaseUntil = now.plus(Duration.ofMillis(claimLeaseMs));
        int claimed = taskRepository.claim(
                taskId,
                PENDING,
                PROCESSING,
                now,
                leaseUntil,
                maxAttempts);
        if (claimed != 1) {
            return Optional.empty();
        }
        S3DeleteTask task = taskRepository.findById(taskId).orElse(null);
        if (task == null) {
            return Optional.empty();
        }
        return Optional.of(new ClaimedTask(task.getId(), task.getBucket(), task.getS3Key(), task.getAttempts()));
    }

    private void markDone(ClaimedTask task) {
        try {
            transactionTemplate.executeWithoutResult(status ->
                    taskRepository.markDone(task.id(), PROCESSING, DONE));
        } catch (RuntimeException ignored) {
        }
    }

    private void handleFailure(ClaimedTask task, boolean retryable, String error) {
        boolean retry = retryable && task.attempt() < maxAttempts;
        try {
            transactionTemplate.executeWithoutResult(status -> {
                if (retry) {
                    taskRepository.scheduleRetry(
                            task.id(),
                            PROCESSING,
                            PENDING,
                            nextAttemptAt(task.attempt()),
                            error);
                } else {
                    taskRepository.markFailed(task.id(), PROCESSING, FAILED, error);
                }
            });
        } catch (RuntimeException ignored) {
        }
    }

    private LocalDateTime nextAttemptAt(int attempt) {
        long delay = Math.min(retryBackoffMs, MAX_RETRY_DELAY_MS);
        for (int i = 1; i < attempt; i++) {
            delay = delay > MAX_RETRY_DELAY_MS / 2
                    ? MAX_RETRY_DELAY_MS
                    : Math.min(MAX_RETRY_DELAY_MS, delay * 2);
        }
        return LocalDateTime.now().plus(Duration.ofMillis(delay));
    }

    private boolean isRetryableStatus(int status) {
        return status == 408 || status == 425 || status == 429 || status >= 500;
    }

    private String formatError(int status, S3Exception error) {
        return "HTTP " + status + ": " + error.getMessage();
    }

    private String formatError(RuntimeException error) {
        return error.getClass().getSimpleName() + ": " + error.getMessage();
    }

    private record ClaimedTask(Long id, String bucket, String s3Key, int attempt) {
    }
}
