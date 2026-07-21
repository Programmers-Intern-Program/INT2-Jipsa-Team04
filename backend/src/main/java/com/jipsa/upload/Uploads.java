package com.jipsa.upload;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.hibernate.annotations.CreationTimestamp;

import java.time.LocalDateTime;

@Entity
@Table(name = "Uploads")
@Getter
@Setter
@NoArgsConstructor
public class Uploads {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(name = "Uploads_IDX")
    private Long id;

    @Column(name = "Users_IDX", nullable = false)
    private Long usersId;

    @Enumerated(EnumType.STRING)
    @Column(name = "Status", nullable = false, length = 30)
    private UploadStatus status = UploadStatus.PENDING;

    @Column(name = "Total", nullable = false)
    private Integer total = 0;

    @CreationTimestamp
    @Column(name = "Created_At", updatable = false)
    private LocalDateTime createdAt;

    @Column(name = "Finished_At")
    private LocalDateTime finishedAt;

    @Column(name = "Idempotency_Key", length = 255)
    private String idempotencyKey;
}