package com.jipsa.purge;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.hibernate.annotations.CreationTimestamp;
import org.hibernate.annotations.UpdateTimestamp;

import java.time.LocalDateTime;

@Entity
@Table(name = "S3_Delete_Task")
@Getter
@Setter
@NoArgsConstructor
public class S3DeleteTask {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(name = "S3_Delete_Task_IDX")
    private Long id;

    @Column(name = "File_IDX", nullable = false)
    private Long fileId;

    @Column(name = "Users_IDX", nullable = false)
    private Long usersId;

    @Column(name = "Bucket", nullable = false, length = 255)
    private String bucket;

    @Column(name = "S3_Key", nullable = false, length = 1024)
    private String s3Key;

    @Column(name = "Status", nullable = false, length = 30)
    private String status = "PENDING";

    @Column(name = "Attempts", nullable = false)
    private Integer attempts = 0;

    @Column(name = "Next_Attempt_At")
    private LocalDateTime nextAttemptAt;

    @Column(name = "Last_Error", columnDefinition = "TEXT")
    private String lastError;

    @CreationTimestamp
    @Column(name = "Created_At", updatable = false)
    private LocalDateTime createdAt;

    @UpdateTimestamp
    @Column(name = "Updated_At")
    private LocalDateTime updatedAt;
}
