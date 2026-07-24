package com.jipsa.chunk;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.hibernate.annotations.CreationTimestamp;

import java.time.LocalDateTime;

@Entity
@Table(name = "Chunk")
@Getter
@Setter
@NoArgsConstructor
public class Chunk {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(name = "Chunk_IDX")
    private Long id;

    @Column(name = "Chunk_ID", nullable = false, length = 128)
    private String chunkId;

    @Column(name = "File_IDX", nullable = false)
    private Long fileId;

    @Column(name = "Chunk_Index", nullable = false)
    private Integer chunkIndex;

    @Column(name = "Content", nullable = false, columnDefinition = "TEXT")
    private String content;

    @Column(name = "Page")
    private Integer page;

    @Column(name = "Index_Version", nullable = false)
    private Integer indexVersion = 1;

    @CreationTimestamp
    @Column(name = "Created_At", updatable = false)
    private LocalDateTime createdAt;
}