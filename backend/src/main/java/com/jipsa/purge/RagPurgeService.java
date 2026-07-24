package com.jipsa.purge;

import com.jipsa.job.RagIngestClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.List;

@Service
public class RagPurgeService {

    private static final Logger log = LoggerFactory.getLogger(RagPurgeService.class);

    private final RagPurgeTaskRepository taskRepository;
    private final RagIngestClient ragIngestClient;
    private final long retryBackoffMs;

    public RagPurgeService(RagPurgeTaskRepository taskRepository,
                           RagIngestClient ragIngestClient,
                           @Value("${app.rag.purge.retry-backoff-ms:60000}") long retryBackoffMs) {
        this.taskRepository = taskRepository;
        this.ragIngestClient = ragIngestClient;
        this.retryBackoffMs = retryBackoffMs;
    }

    public void enqueue(Long fileId, Long usersId) {
        RagPurgeTask task = new RagPurgeTask();
        task.setFileId(fileId);
        task.setUsersId(usersId);
        task.setNextAttemptAt(LocalDateTime.now());
        taskRepository.save(task);
    }

    @Transactional
    public void drainOnce() {
        List<RagPurgeTask> tasks = taskRepository
                .findTop50ByStatusAndNextAttemptAtBeforeOrderByNextAttemptAt("PENDING", LocalDateTime.now());
        for (RagPurgeTask task : tasks) {
            try {
                ragIngestClient.purge(task.getFileId(), task.getUsersId());
                task.setStatus("DONE");
                task.setLastError(null);
            } catch (RuntimeException e) {
                task.setAttempts(task.getAttempts() + 1);
                task.setLastError(e.getMessage());
                task.setNextAttemptAt(LocalDateTime.now().plus(Duration.ofMillis(retryBackoffMs)));
                log.warn("RAG purge 재시도 예정 (file {}, attempt {}): {}",
                        task.getFileId(), task.getAttempts(), e.getMessage());
            }
        }
    }
}