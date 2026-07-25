package com.jipsa.metadata;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.jipsa.chunk.Chunk;
import com.jipsa.chunk.ChunkRepository;
import com.jipsa.file.File;
import com.jipsa.file.FileMetadata;
import com.jipsa.file.FileMetadataRepository;
import com.jipsa.file.FileRepository;
import com.jipsa.file.FileStatus;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

import java.time.LocalDateTime;
import java.util.List;

@Service
public class AutoMetadataService {

    private static final Logger log = LoggerFactory.getLogger(AutoMetadataService.class);

    private final FileRepository fileRepository;
    private final FileMetadataRepository fileMetadataRepository;
    private final ChunkRepository chunkRepository;
    private final AutoMetadataClient autoMetadataClient;
    private final ObjectMapper objectMapper;
    private final TransactionTemplate transactionTemplate;
    private final int maxChunks;
    private final int maxChars;

    public AutoMetadataService(FileRepository fileRepository,
                               FileMetadataRepository fileMetadataRepository,
                               ChunkRepository chunkRepository,
                               AutoMetadataClient autoMetadataClient,
                               ObjectMapper objectMapper,
                               PlatformTransactionManager transactionManager,
                               @Value("${app.metadata.ai.max-chunks:6}") int maxChunks,
                               @Value("${app.metadata.ai.max-chars:4000}") int maxChars) {
        this.fileRepository = fileRepository;
        this.fileMetadataRepository = fileMetadataRepository;
        this.chunkRepository = chunkRepository;
        this.autoMetadataClient = autoMetadataClient;
        this.objectMapper = objectMapper;
        this.transactionTemplate = new TransactionTemplate(transactionManager);
        this.maxChunks = maxChunks;
        this.maxChars = maxChars;
    }

    public void process(Long fileId) {
        String sample = transactionTemplate.execute(status -> claim(fileId));
        if (sample == null || sample.isBlank()) {
            return;
        }
        AutoMetadataResult result;
        try {
            result = autoMetadataClient.generate(sample);
        } catch (RuntimeException e) {
            log.warn("AI 메타데이터 생성 실패 (file {}): {}", fileId, e.getMessage());
            transactionTemplate.executeWithoutResult(status -> markFailed(fileId));
            return;
        }
        transactionTemplate.executeWithoutResult(status -> persist(fileId, result));
        log.info("AI 메타데이터 생성 완료 (file {})", fileId);
    }

    private String claim(Long fileId) {
        FileMetadata metadata = fileMetadataRepository.findById(fileId).orElse(null);
        if (metadata == null || !"PROCESSING".equals(metadata.getExtractionStatus())) {
            return null;
        }
        File file = fileRepository.findByIdAndDeletedAtIsNull(fileId).orElse(null);
        if (file == null || file.getStatus() != FileStatus.READY) {
            return null;
        }
        String sample = buildSample(fileId);
        if (sample.isBlank()) {
            return null;
        }
        metadata.setExtractionStatus("GENERATING");
        fileMetadataRepository.save(metadata);
        return sample;
    }

    private String buildSample(Long fileId) {
        List<Chunk> chunks = chunkRepository.findByFileIdOrderByChunkIndexAsc(fileId, PageRequest.of(0, maxChunks));
        StringBuilder builder = new StringBuilder();
        for (Chunk chunk : chunks) {
            if (chunk.getContent() == null) {
                continue;
            }
            if (builder.length() > 0) {
                builder.append("\n\n");
            }
            builder.append(chunk.getContent());
            if (builder.length() >= maxChars) {
                break;
            }
        }
        if (builder.length() > maxChars) {
            return builder.substring(0, maxChars);
        }
        return builder.toString();
    }

    private void persist(Long fileId, AutoMetadataResult result) {
        FileMetadata metadata = fileMetadataRepository.findById(fileId).orElse(null);
        if (metadata == null) {
            return;
        }
        metadata.setSummary(result.summary());
        metadata.setKeywords(writeJson(result.keywords()));
        metadata.setExtractedEntities(writeJson(result.entities()));
        metadata.setExtractionConfidence(result.confidence());
        if ((metadata.getDocumentType() == null || metadata.getDocumentType().isBlank())
                && result.documentType() != null && !result.documentType().isBlank()) {
            metadata.setDocumentType(result.documentType().trim());
        }
        metadata.setExtractionStatus("READY");
        metadata.setUpdatedAt(LocalDateTime.now());
        fileMetadataRepository.save(metadata);
    }

    private void markFailed(Long fileId) {
        fileMetadataRepository.findById(fileId).ifPresent(metadata -> {
            metadata.setExtractionStatus("FAILED");
            metadata.setUpdatedAt(LocalDateTime.now());
            fileMetadataRepository.save(metadata);
        });
    }

    private String writeJson(Object value) {
        if (value == null) {
            return null;
        }
        try {
            return objectMapper.writeValueAsString(value);
        } catch (JsonProcessingException e) {
            return null;
        }
    }
}