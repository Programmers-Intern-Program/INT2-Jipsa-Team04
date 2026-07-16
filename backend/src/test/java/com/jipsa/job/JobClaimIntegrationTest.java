package com.jipsa.job;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.data.jpa.test.autoconfigure.DataJpaTest;
import org.springframework.data.domain.PageRequest;

import java.time.LocalDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

@DataJpaTest
class JobClaimIntegrationTest {

    @Autowired
    private JobRepository jobRepository;

    private Job persist(JobStatus status, LocalDateTime nextAttemptAt, LocalDateTime ownershipExpiresAt) {
        Job job = new Job();
        job.setJobType(JobType.INGEST);
        job.setJobStatus(status);
        job.setNextAttemptAt(nextAttemptAt);
        job.setOwnershipExpiresAt(ownershipExpiresAt);
        return jobRepository.saveAndFlush(job);
    }

    @Test
    void onlyOneWorkerClaimsAJob() {
        Job job = persist(JobStatus.PENDING, null, null);
        LocalDateTime now = LocalDateTime.now();

        int first = jobRepository.claim(job.getId(), "worker-1", now, now.plusMinutes(1));
        int second = jobRepository.claim(job.getId(), "worker-2", now, now.plusMinutes(1));

        assertThat(first).isEqualTo(1);
        assertThat(second).isEqualTo(0);

        Job reloaded = jobRepository.findById(job.getId()).orElseThrow();
        assertThat(reloaded.getJobStatus()).isEqualTo(JobStatus.RUNNING);
        assertThat(reloaded.getWorkerId()).isEqualTo("worker-1");
        assertThat(reloaded.getAttempts()).isEqualTo(1);
    }

    @Test
    void claimablePicksPendingDueRetryAndExpiredRunning() {
        LocalDateTime now = LocalDateTime.now();
        Job pending = persist(JobStatus.PENDING, null, null);
        Job dueRetry = persist(JobStatus.RETRY_WAIT, now.minusMinutes(1), null);
        Job futureRetry = persist(JobStatus.RETRY_WAIT, now.plusMinutes(5), null);
        Job expiredRunning = persist(JobStatus.RUNNING, null, now.minusMinutes(1));
        Job liveRunning = persist(JobStatus.RUNNING, null, now.plusMinutes(5));
        Job done = persist(JobStatus.SUCCESS, null, null);

        List<Long> ids = jobRepository.findClaimableIds(now, PageRequest.of(0, 10));

        assertThat(ids).contains(pending.getId(), dueRetry.getId(), expiredRunning.getId());
        assertThat(ids).doesNotContain(futureRetry.getId(), liveRunning.getId(), done.getId());
    }
}