package com.jipsa.purge;

import com.jipsa.file.S3Service;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.data.jpa.test.autoconfigure.DataJpaTest;
import org.springframework.transaction.PlatformTransactionManager;
import software.amazon.awssdk.services.s3.model.S3Exception;

import java.time.LocalDateTime;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

@DataJpaTest
class S3DeleteServiceTest {

    @Autowired
    private S3DeleteTaskRepository taskRepository;

    @Autowired
    private PlatformTransactionManager transactionManager;

    private S3Service s3Service;
    private S3DeleteService deleteService;

    @BeforeEach
    void setUp() {
        s3Service = mock(S3Service.class);
        deleteService = new S3DeleteService(
                taskRepository,
                s3Service,
                "test-bucket",
                1L,
                2,
                300_000L,
                120_000L,
                transactionManager);
    }

    @Test
    void enqueuePersistsBucketAndKeyBeforeFileDeletion() {
        deleteService.enqueue(10L, 7L, "files/key-1");

        S3DeleteTask task = taskRepository.findAll().getFirst();
        assertThat(task.getFileId()).isEqualTo(10L);
        assertThat(task.getUsersId()).isEqualTo(7L);
        assertThat(task.getBucket()).isEqualTo("test-bucket");
        assertThat(task.getS3Key()).isEqualTo("files/key-1");
        assertThat(task.getStatus()).isEqualTo("PENDING");
    }

    @Test
    void transientFailureStopsAfterMaximumAttempts() {
        S3DeleteTask task = persist("PENDING", 0, LocalDateTime.now().minusMinutes(1));
        doThrow(new RuntimeException("S3 unavailable"))
                .when(s3Service).delete("test-bucket", "files/key-1");

        deleteService.drainOnce();
        task = reload(task);
        assertThat(task.getStatus()).isEqualTo("PENDING");
        assertThat(task.getAttempts()).isEqualTo(1);

        task.setNextAttemptAt(LocalDateTime.now().minusMinutes(1));
        taskRepository.saveAndFlush(task);
        deleteService.drainOnce();

        task = reload(task);
        assertThat(task.getStatus()).isEqualTo("FAILED");
        assertThat(task.getAttempts()).isEqualTo(2);
        verify(s3Service, org.mockito.Mockito.times(2)).delete("test-bucket", "files/key-1");
    }

    @Test
    void missingObjectIsAlreadyDeleted() {
        S3DeleteTask task = persist("PENDING", 0, LocalDateTime.now().minusMinutes(1));
        doThrow(S3Exception.builder().statusCode(404).message("Not Found").build())
                .when(s3Service).delete("test-bucket", "files/key-1");

        deleteService.drainOnce();

        assertThat(reload(task).getStatus()).isEqualTo("DONE");
        verify(s3Service).delete("test-bucket", "files/key-1");
    }

    @Test
    void permanentHttpFailureDoesNotRetry() {
        S3DeleteTask task = persist("PENDING", 0, LocalDateTime.now().minusMinutes(1));
        doThrow(S3Exception.builder().statusCode(403).message("Forbidden").build())
                .when(s3Service).delete("test-bucket", "files/key-1");

        deleteService.drainOnce();

        assertThat(reload(task).getStatus()).isEqualTo("FAILED");
        verify(s3Service).delete("test-bucket", "files/key-1");
    }

    @Test
    void claimAllowsOnlyOneWorkerToProcessTask() {
        S3DeleteTask task = persist("PENDING", 0, LocalDateTime.now().minusMinutes(1));
        LocalDateTime now = LocalDateTime.now();

        int first = taskRepository.claim(task.getId(), "PENDING", "PROCESSING", now, now.plusMinutes(5), 2);
        int second = taskRepository.claim(task.getId(), "PENDING", "PROCESSING", now, now.plusMinutes(5), 2);

        assertThat(first).isEqualTo(1);
        assertThat(second).isEqualTo(0);
        assertThat(reload(task).getAttempts()).isEqualTo(1);
    }

    private S3DeleteTask persist(String status, int attempts, LocalDateTime nextAttemptAt) {
        S3DeleteTask task = new S3DeleteTask();
        task.setFileId(10L);
        task.setUsersId(7L);
        task.setBucket("test-bucket");
        task.setS3Key("files/key-1");
        task.setStatus(status);
        task.setAttempts(attempts);
        task.setNextAttemptAt(nextAttemptAt);
        return taskRepository.saveAndFlush(task);
    }

    private S3DeleteTask reload(S3DeleteTask task) {
        return taskRepository.findById(task.getId()).orElseThrow();
    }
}
