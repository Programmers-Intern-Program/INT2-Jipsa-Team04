package com.jipsa.job;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.hibernate.annotations.CreationTimestamp;

import java.time.LocalDateTime;

@Entity
@Table(name = "Job")
@Getter
@Setter
@NoArgsConstructor
public class Job {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(name = "Job_IDX")
    private Long id;

    @Column(name = "File_IDX")
    private Long fileId;

    @Column(name = "Uploads_IDX")
    private Long uploadsId;

    @Enumerated(EnumType.STRING)
    @Column(name = "Job_Type", nullable = false, length = 50)
    private JobType jobType;

    @Enumerated(EnumType.STRING)
    @Column(name = "Job_Status", nullable = false, length = 50)
    private JobStatus jobStatus = JobStatus.PENDING;

    @Column(name = "Priority", nullable = false)
    private Integer priority = 0;

    @Column(name = "Attempts", nullable = false)
    private Integer attempts = 0;

    @Column(name = "Max_Attempts", nullable = false)
    private Integer maxAttempts = 3;

    @Column(name = "Error_Message", columnDefinition = "TEXT")
    private String errorMessage;

    @Column(name = "Next_Attempt_At")
    private LocalDateTime nextAttemptAt;

    @Column(name = "Worker_ID", length = 64)
    private String workerId;

    @Column(name = "Ownership_Expires_At")
    private LocalDateTime ownershipExpiresAt;

    @CreationTimestamp
    @Column(name = "Created_At", updatable = false)
    private LocalDateTime createdAt;

    @Column(name = "Started_At")
    private LocalDateTime startedAt;

    @Column(name = "Finished_At")
    private LocalDateTime finishedAt;
}