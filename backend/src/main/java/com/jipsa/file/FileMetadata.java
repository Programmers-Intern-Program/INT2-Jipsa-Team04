package com.jipsa.file;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.hibernate.annotations.CreationTimestamp;
import org.hibernate.annotations.UpdateTimestamp;

import java.time.LocalDateTime;

@Entity
@Table(name = "File_Metadata")
@Getter
@Setter
@NoArgsConstructor
public class FileMetadata {

    @Id
    @Column(name = "File_IDX")
    private Long fileId;

    @Column(name = "File_Type", nullable = false, length = 50)
    private String fileType;

    @Column(name = "Summary")
    private String summary;

    @Column(name = "Tags")
    private String tags;

    @Column(name = "Keywords")
    private String keywords;

    @Column(name = "Document_Type", length = 100)
    private String documentType;

    @Column(name = "Extraction_Status", length = 30)
    private String extractionStatus;

    @Column(name = "Extraction_Confidence")
    private Double extractionConfidence;

    @Column(name = "Extracted_Entities")
    private String extractedEntities;

    @CreationTimestamp
    @Column(name = "Created_At", updatable = false)
    private LocalDateTime createdAt;

    @UpdateTimestamp
    @Column(name = "Updated_At")
    private LocalDateTime updatedAt;
}