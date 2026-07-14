package com.jipsa.file;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.hibernate.annotations.CreationTimestamp;
import org.hibernate.annotations.UpdateTimestamp;

import java.time.LocalDateTime;

@Entity
@Table(name = "File")
@Getter
@Setter
@NoArgsConstructor
public class File {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(name = "File_IDX")
    private Long id;

    @Column(name = "Users_IDX", nullable = false)
    private Long usersId;

    @Column(name = "Folder_IDX")
    private Long folderId;

    @Column(name = "Uploads_IDX")
    private Long uploadsId;

    @Column(name = "Name", nullable = false, length = 255)
    private String name;

    @Column(name = "S3_Key", nullable = false, length = 512, unique = true)
    private String s3Key;

    @Column(name = "File_Type", nullable = false, length = 50)
    private String fileType;

    @Column(name = "Size_Bytes", nullable = false)
    private Long sizeBytes = 0L;

    @Enumerated(EnumType.STRING)
    @Column(name = "Status", nullable = false, length = 30)
    private FileStatus status = FileStatus.UPLOADED;

    @Column(name = "Security_Rank", length = 30)
    private String securityRank;

    @Column(name = "PII_Detected", nullable = false)
    private boolean piiDetected = false;

    @Column(name = "Error_Message", columnDefinition = "TEXT")
    private String errorMessage;

    @Column(name = "Owner_Message", length = 255)
    private String ownerMessage;

    @Column(name = "Owner_Name", length = 255)
    private String ownerName;

    @Column(name = "Star", nullable = false)
    private boolean star = false;

    @Column(name = "Processing_Stage", length = 50)
    private String processingStage;

    @CreationTimestamp
    @Column(name = "Created_At", updatable = false)
    private LocalDateTime createdAt;

    @UpdateTimestamp
    @Column(name = "Updated_At")
    private LocalDateTime updatedAt;

    @Column(name = "Deleted_At")
    private LocalDateTime deletedAt;
}