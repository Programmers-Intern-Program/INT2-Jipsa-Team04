package com.jipsa.purge;

import com.jipsa.job.RagIngestClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.boot.data.jpa.test.autoconfigure.DataJpaTest;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.web.client.HttpClientErrorException;

import java.nio.charset.StandardCharsets;
import java.time.LocalDateTime;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;

@DataJpaTest
class RagPurgeServiceTest {

    @Autowired
    private RagPurgeTaskRepository taskRepository;

    @Autowired
    private PlatformTransactionManager transactionManager;

    private RagIngestClient ragIngestClient;
    private RagPurgeService purgeService;

    @BeforeEach
    void setUp() {
        ragIngestClient = mock(RagIngestClient.class);
        purgeService = new RagPurgeService(taskRepository, ragIngestClient, 1L, 2, 300_000L, 120_000L, transactionManager);
    }

    @Test
    void transientFailureStopsAfterMaximumAttempts() {
        RagPurgeTask task = persist("PENDING", 0, LocalDateTime.now().minusMinutes(1));
        doThrow(new RuntimeException("RAG unavailable")).when(ragIngestClient).purge(10L, 7L);

        purgeService.drainOnce();
        task = reload(task);
        assertThat(task.getStatus()).isEqualTo("PENDING");
        assertThat(task.getAttempts()).isEqualTo(1);

        task.setNextAttemptAt(LocalDateTime.now().minusMinutes(1));
        taskRepository.saveAndFlush(task);
        purgeService.drainOnce();

        task = reload(task);
        assertThat(task.getStatus()).isEqualTo("FAILED");
        assertThat(task.getAttempts()).isEqualTo(2);
        verify(ragIngestClient, org.mockito.Mockito.times(2)).purge(10L, 7L);
    }

    @Test
    void permanentHttpFailureDoesNotRetry() {
        RagPurgeTask task = persist("PENDING", 0, LocalDateTime.now().minusMinutes(1));
        HttpClientErrorException error = HttpClientErrorException.create(
                HttpStatus.BAD_REQUEST,
                "Bad Request",
                HttpHeaders.EMPTY,
                new byte[0],
                StandardCharsets.UTF_8);
        doThrow(error).when(ragIngestClient).purge(10L, 7L);

        purgeService.drainOnce();

        assertThat(reload(task).getStatus()).isEqualTo("FAILED");
        verify(ragIngestClient).purge(10L, 7L);
    }

    @Test
    void notFoundResponseDoesNotRetry() {
        RagPurgeTask task = persist("PENDING", 0, LocalDateTime.now().minusMinutes(1));
        HttpClientErrorException error = HttpClientErrorException.create(
                HttpStatus.NOT_FOUND,
                "Not Found",
                HttpHeaders.EMPTY,
                new byte[0],
                StandardCharsets.UTF_8);
        doThrow(error).when(ragIngestClient).purge(10L, 7L);

        purgeService.drainOnce();

        assertThat(reload(task).getStatus()).isEqualTo("FAILED");
        verify(ragIngestClient).purge(10L, 7L);
    }

    @Test
    void expiredClaimIsRecoveredWithoutLeavingStuckTask() {
        RagPurgeTask task = persist("PROCESSING", 2, LocalDateTime.now().minusMinutes(1));

        purgeService.drainOnce();

        assertThat(reload(task).getStatus()).isEqualTo("FAILED");
        verifyNoInteractions(ragIngestClient);
    }

    @Test
    void claimAllowsOnlyOneWorkerToProcessTask() {
        RagPurgeTask task = persist("PENDING", 0, LocalDateTime.now().minusMinutes(1));
        LocalDateTime now = LocalDateTime.now();

        int first = taskRepository.claim(task.getId(), "PENDING", "PROCESSING", now, now.plusMinutes(5), 2);
        int second = taskRepository.claim(task.getId(), "PENDING", "PROCESSING", now, now.plusMinutes(5), 2);

        assertThat(first).isEqualTo(1);
        assertThat(second).isEqualTo(0);
        assertThat(reload(task).getAttempts()).isEqualTo(1);
    }

    private RagPurgeTask persist(String status, int attempts, LocalDateTime nextAttemptAt) {
        RagPurgeTask task = new RagPurgeTask();
        task.setFileId(10L);
        task.setUsersId(7L);
        task.setStatus(status);
        task.setAttempts(attempts);
        task.setNextAttemptAt(nextAttemptAt);
        return taskRepository.saveAndFlush(task);
    }

    private RagPurgeTask reload(RagPurgeTask task) {
        return taskRepository.findById(task.getId()).orElseThrow();
    }
}
