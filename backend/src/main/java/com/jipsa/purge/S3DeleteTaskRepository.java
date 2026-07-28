package com.jipsa.purge;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.LocalDateTime;
import java.util.List;

public interface S3DeleteTaskRepository extends JpaRepository<S3DeleteTask, Long> {

    List<S3DeleteTask> findTop50ByStatusAndNextAttemptAtBeforeOrderByNextAttemptAt(String status, LocalDateTime before);

    @Modifying(flushAutomatically = true, clearAutomatically = true)
    @Query("""
            update S3DeleteTask t
            set t.status = :processing,
                t.attempts = t.attempts + 1,
                t.nextAttemptAt = :leaseUntil
            where t.id = :id
              and t.status = :pending
              and (t.nextAttemptAt is null or t.nextAttemptAt <= :now)
              and t.attempts < :maxAttempts
            """)
    int claim(@Param("id") Long id,
              @Param("pending") String pending,
              @Param("processing") String processing,
              @Param("now") LocalDateTime now,
              @Param("leaseUntil") LocalDateTime leaseUntil,
              @Param("maxAttempts") int maxAttempts);

    @Modifying(flushAutomatically = true, clearAutomatically = true)
    @Query("""
            update S3DeleteTask t
            set t.status = :done,
                t.nextAttemptAt = null,
                t.lastError = null
            where t.id = :id and t.status = :processing
            """)
    int markDone(@Param("id") Long id,
                 @Param("processing") String processing,
                 @Param("done") String done);

    @Modifying(flushAutomatically = true, clearAutomatically = true)
    @Query("""
            update S3DeleteTask t
            set t.status = :pending,
                t.nextAttemptAt = :nextAttemptAt,
                t.lastError = :lastError
            where t.id = :id and t.status = :processing
            """)
    int scheduleRetry(@Param("id") Long id,
                      @Param("processing") String processing,
                      @Param("pending") String pending,
                      @Param("nextAttemptAt") LocalDateTime nextAttemptAt,
                      @Param("lastError") String lastError);

    @Modifying(flushAutomatically = true, clearAutomatically = true)
    @Query("""
            update S3DeleteTask t
            set t.status = :failed,
                t.nextAttemptAt = null,
                t.lastError = :lastError
            where t.id = :id and t.status = :processing
            """)
    int markFailed(@Param("id") Long id,
                   @Param("processing") String processing,
                   @Param("failed") String failed,
                   @Param("lastError") String lastError);

    @Modifying(flushAutomatically = true, clearAutomatically = true)
    @Query("""
            update S3DeleteTask t
            set t.status = :pending,
                t.nextAttemptAt = :now,
                t.lastError = :lastError
            where t.status = :processing
              and t.nextAttemptAt is not null
              and t.nextAttemptAt <= :now
              and t.attempts < :maxAttempts
            """)
    int requeueExpiredClaims(@Param("processing") String processing,
                             @Param("pending") String pending,
                             @Param("now") LocalDateTime now,
                             @Param("maxAttempts") int maxAttempts,
                             @Param("lastError") String lastError);

    @Modifying(flushAutomatically = true, clearAutomatically = true)
    @Query("""
            update S3DeleteTask t
            set t.status = :failed,
                t.nextAttemptAt = null,
                t.lastError = :lastError
            where t.status = :processing
              and t.nextAttemptAt is not null
              and t.nextAttemptAt <= :now
              and t.attempts >= :maxAttempts
            """)
    int failExpiredClaims(@Param("processing") String processing,
                          @Param("failed") String failed,
                          @Param("now") LocalDateTime now,
                          @Param("maxAttempts") int maxAttempts,
                          @Param("lastError") String lastError);

    @Modifying(flushAutomatically = true, clearAutomatically = true)
    @Query("""
            update S3DeleteTask t
            set t.status = :failed,
                t.nextAttemptAt = null,
                t.lastError = :lastError
            where t.status = :pending and t.attempts >= :maxAttempts
            """)
    int failExhaustedPending(@Param("pending") String pending,
                             @Param("failed") String failed,
                             @Param("maxAttempts") int maxAttempts,
                             @Param("lastError") String lastError);
}
