package com.jipsa.purge;

import com.jipsa.job.RagIngestClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.client.RestClientResponseException;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

@Service
public class RagPurgeService {

    private static final String PENDING = "PENDING";
    private static final String PROCESSING = "PROCESSING";
    private static final String DONE = "DONE";
    private static final String FAILED = "FAILED";
    private static final long MAX_RETRY_DELAY_MS = Duration.ofHours(1).toMillis();

    private static final Logger log = LoggerFactory.getLogger(RagPurgeService.class);

    private final RagPurgeTaskRepository taskRepository;
    private final RagIngestClient ragIngestClient;
    private final long retryBackoffMs;
    private final int maxAttempts;
    private final long claimLeaseMs;
    private final TransactionTemplate transactionTemplate;

    public RagPurgeService(RagPurgeTaskRepository taskRepository,
                           RagIngestClient ragIngestClient,
                           @Value("${app.rag.purge.retry-backoff-ms:60000}") long retryBackoffMs,
                           @Value("${app.rag.purge.max-attempts:5}") int maxAttempts,
                           @Value("${app.rag.purge.claim-lease-ms:300000}") long claimLeaseMs,
                           @Value("${app.rag.read-timeout-ms:120000}") long ragReadTimeoutMs,
                           PlatformTransactionManager transactionManager) {
        this.taskRepository = taskRepository;
        this.ragIngestClient = ragIngestClient;
        this.retryBackoffMs = Math.max(1L, retryBackoffMs);
        this.maxAttempts = Math.max(1, maxAttempts);
        this.claimLeaseMs = Math.max(Math.max(1L, claimLeaseMs), Math.max(1L, ragReadTimeoutMs) + 30_000L);
        this.transactionTemplate = new TransactionTemplate(transactionManager);
    }

    public void enqueue(Long fileId, Long usersId) {
        RagPurgeTask task = new RagPurgeTask();
        task.setFileId(fileId);
        task.setUsersId(usersId);
        task.setNextAttemptAt(LocalDateTime.now());
        taskRepository.saveAndFlush(task);
    }

    public void drainOnce() {
        LocalDateTime now = LocalDateTime.now();
        try {
            transactionTemplate.executeWithoutResult(status -> recoverExpiredTasks(now));
        } catch (RuntimeException e) {
            log.warn("RAG purge 작업 복구에 실패했습니다: {}", e.getMessage());
            return;
        }

        List<RagPurgeTask> tasks;
        try {
            tasks = taskRepository.findTop50ByStatusAndNextAttemptAtBeforeOrderByNextAttemptAt(PENDING, now);
        } catch (RuntimeException e) {
            log.warn("RAG purge 작업 조회에 실패했습니다: {}", e.getMessage());
            return;
        }

        for (RagPurgeTask task : tasks) {
            process(task.getId());
        }
    }

    private void recoverExpiredTasks(LocalDateTime now) {
        taskRepository.failExpiredClaims(
                PROCESSING,
                FAILED,
                now,
                maxAttempts,
                "purge 작업 선점이 만료되었고 최대 재시도 횟수를 초과했습니다.");
        taskRepository.requeueExpiredClaims(
                PROCESSING,
                PENDING,
                now,
                maxAttempts,
                "purge 작업 선점이 만료되어 재시도합니다.");
        taskRepository.failExhaustedPending(
                PENDING,
                FAILED,
                maxAttempts,
                "purge 최대 재시도 횟수를 초과했습니다.");
    }

    private void process(Long taskId) {
        Optional<ClaimedTask> claimed;
        try {
            claimed = transactionTemplate.execute(status -> claim(taskId, LocalDateTime.now()));
        } catch (RuntimeException e) {
            log.warn("RAG purge 작업 선점에 실패했습니다 (task {}): {}", taskId, e.getMessage());
            return;
        }
        if (claimed == null || claimed.isEmpty()) {
            return;
        }

        ClaimedTask task = claimed.get();
        try {
            ragIngestClient.purge(task.fileId(), task.usersId());
            markDone(task);
        } catch (RestClientResponseException e) {
            int status = e.getStatusCode().value();
            if (status == 410) {
                markDone(task);
                return;
            }
            handleFailure(task, isRetryableStatus(status), formatHttpError(status, e));
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
        RagPurgeTask task = taskRepository.findById(taskId).orElse(null);
        if (task == null) {
            return Optional.empty();
        }
        return Optional.of(new ClaimedTask(task.getId(), task.getFileId(), task.getUsersId(), task.getAttempts()));
    }

    private void markDone(ClaimedTask task) {
        try {
            transactionTemplate.executeWithoutResult(status -> {
                int updated = taskRepository.markDone(task.id(), PROCESSING, DONE);
                if (updated != 1) {
                    log.warn("RAG purge 완료 상태를 반영하지 못했습니다 (task {})", task.id());
                }
            });
        } catch (RuntimeException e) {
            log.warn("RAG purge 완료 상태 저장에 실패했습니다 (task {}): {}", task.id(), e.getMessage());
        }
    }

    private void handleFailure(ClaimedTask task, boolean retryable, String error) {
        boolean retry = retryable && task.attempt() < maxAttempts;
        try {
            transactionTemplate.executeWithoutResult(status -> {
                int updated;
                if (retry) {
                    updated = taskRepository.scheduleRetry(
                            task.id(),
                            PROCESSING,
                            PENDING,
                            nextAttemptAt(task.attempt()),
                            error);
                } else {
                    updated = taskRepository.markFailed(task.id(), PROCESSING, FAILED, error);
                }
                if (updated != 1) {
                    log.warn("RAG purge 실패 상태를 반영하지 못했습니다 (task {})", task.id());
                }
            });
        } catch (RuntimeException e) {
            log.warn("RAG purge 실패 상태 저장에 실패했습니다 (task {}): {}", task.id(), e.getMessage());
            return;
        }
        if (retry) {
            log.warn("RAG purge 재시도 예정 (file {}, attempt {} / {}): {}",
                    task.fileId(), task.attempt(), maxAttempts, error);
        } else {
            log.warn("RAG purge를 종료했습니다 (file {}, attempt {} / {}): {}",
                    task.fileId(), task.attempt(), maxAttempts, error);
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

    private String formatHttpError(int status, RestClientResponseException error) {
        String message = error.getMessage();
        return "HTTP " + status + (message == null || message.isBlank() ? "" : ": " + message);
    }

    private String formatError(RuntimeException error) {
        String message = error.getMessage();
        return error.getClass().getSimpleName()
                + (message == null || message.isBlank() ? "" : ": " + message);
    }

    private record ClaimedTask(Long id, Long fileId, Long usersId, int attempt) {
    }
}
