package com.jipsa.purge;

import org.springframework.data.jpa.repository.JpaRepository;

import java.time.LocalDateTime;
import java.util.List;

public interface RagPurgeTaskRepository extends JpaRepository<RagPurgeTask, Long> {

    List<RagPurgeTask> findTop50ByStatusAndNextAttemptAtBeforeOrderByNextAttemptAt(String status, LocalDateTime before);
}