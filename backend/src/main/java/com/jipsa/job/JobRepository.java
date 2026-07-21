package com.jipsa.job;

import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

public interface JobRepository extends JpaRepository<Job, Long> {

    Optional<Job> findTopByFileIdOrderByCreatedAtDesc(Long fileId);

    @Query("""
            select j.id from Job j
            where j.jobStatus = com.jipsa.job.JobStatus.PENDING
               or (j.jobStatus = com.jipsa.job.JobStatus.RETRY_WAIT and j.nextAttemptAt <= :now)
               or (j.jobStatus = com.jipsa.job.JobStatus.RUNNING and j.ownershipExpiresAt is not null and j.ownershipExpiresAt < :now and j.attempts < j.maxAttempts)
            order by j.priority desc, j.id asc
            """)
    List<Long> findClaimableIds(@Param("now") LocalDateTime now, Pageable pageable);

    @Modifying(clearAutomatically = true)
    @Query("""
            update Job j
            set j.jobStatus = com.jipsa.job.JobStatus.RUNNING,
                j.workerId = :workerId,
                j.startedAt = :now,
                j.ownershipExpiresAt = :expiry,
                j.attempts = j.attempts + 1
            where j.id = :id
              and (j.jobStatus = com.jipsa.job.JobStatus.PENDING
                   or (j.jobStatus = com.jipsa.job.JobStatus.RETRY_WAIT and j.nextAttemptAt <= :now)
                   or (j.jobStatus = com.jipsa.job.JobStatus.RUNNING and j.ownershipExpiresAt is not null and j.ownershipExpiresAt < :now and j.attempts < j.maxAttempts))
            """)
    int claim(@Param("id") Long id, @Param("workerId") String workerId,
              @Param("now") LocalDateTime now, @Param("expiry") LocalDateTime expiry);

    @Query("""
            select j.id from Job j
            where j.jobStatus = com.jipsa.job.JobStatus.RUNNING
              and j.ownershipExpiresAt is not null
              and j.ownershipExpiresAt < :now
              and j.attempts >= j.maxAttempts
            """)
    List<Long> findExpiredExhaustedIds(@Param("now") LocalDateTime now);
}