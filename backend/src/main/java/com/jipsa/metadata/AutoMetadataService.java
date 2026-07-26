package com.jipsa.metadata;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.jipsa.chunk.Chunk;
import com.jipsa.chunk.ChunkRepository;
import com.jipsa.file.FileMetadata;
import com.jipsa.file.FileMetadataRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.List;

@Service
public class AutoMetadataService {

    private static final Logger log = LoggerFactory.getLogger(AutoMetadataService.class);
    private static final int MAX_LIST_ITEMS = 5;

    private final FileMetadataRepository fileMetadataRepository;
    private final ChunkRepository chunkRepository;
    private final AutoMetadataClient autoMetadataClient;
    private final ObjectMapper objectMapper;
    private final MetadataProperties metadataProperties;
    private final TransactionTemplate transactionTemplate;
    private final int maxChunks;
    private final int maxChars;
    private final long staleTimeoutMs;
    private final int maxSummaryChars;

    public AutoMetadataService(FileMetadataRepository fileMetadataRepository,
                               ChunkRepository chunkRepository,
                               AutoMetadataClient autoMetadataClient,
                               ObjectMapper objectMapper,
                               MetadataProperties metadataProperties,
                               PlatformTransactionManager transactionManager,
                               @Value("${app.metadata.ai.max-chunks:6}") int maxChunks,
                               @Value("${app.metadata.ai.max-chars:4000}") int maxChars,
                               @Value("${app.metadata.ai.stale-timeout-ms:300000}") long staleTimeoutMs,
                               @Value("${app.metadata.ai.max-summary-chars:500}") int maxSummaryChars) {
        this.fileMetadataRepository = fileMetadataRepository;
        this.chunkRepository = chunkRepository;
        this.autoMetadataClient = autoMetadataClient;
        this.objectMapper = objectMapper;
        this.metadataProperties = metadataProperties;
        this.transactionTemplate = new TransactionTemplate(transactionManager);
        this.maxChunks = maxChunks;
        this.maxChars = maxChars;
        this.staleTimeoutMs = staleTimeoutMs;
        this.maxSummaryChars = maxSummaryChars;
    }

    public void process(Long fileId) {
        Integer claimed = transactionTemplate.execute(status ->
                fileMetadataRepository.claimForGeneration(fileId, LocalDateTime.now()));
        if (claimed == null || claimed == 0) {
            return;
        }
        String sample = buildSample(fileId);
        if (sample.isBlank()) {
            transactionTemplate.executeWithoutResult(status -> markFailed(fileId));
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
        try {
            transactionTemplate.executeWithoutResult(status -> persist(fileId, result));
        } catch (RuntimeException e) {
            log.warn("AI 메타데이터 저장 실패 (file {}): {}", fileId, e.getMessage());
        }
    }

    public void reapStale() {
        LocalDateTime now = LocalDateTime.now();
        LocalDateTime threshold = now.minus(Duration.ofMillis(staleTimeoutMs));
        transactionTemplate.executeWithoutResult(status ->
                fileMetadataRepository.resetStaleGenerating(threshold, now));
    }

    public void skipPending() {
        transactionTemplate.executeWithoutResult(status ->
                fileMetadataRepository.markPendingSkipped(LocalDateTime.now()));
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
        if (metadata == null || !"GENERATING".equals(metadata.getExtractionStatus())) {
            return;
        }
        Integer claimVersion = metadata.getExtractionIndexVersion();
        Integer latest = chunkRepository.findMaxIndexVersionByFileId(fileId);
        if (claimVersion != null && latest != null && !latest.equals(claimVersion)) {
            return;
        }
        String summary = truncate(result == null ? null : result.summary(), maxSummaryChars);
        if (summary == null || summary.isBlank()) {
            metadata.setExtractionStatus("FAILED");
            metadata.setUpdatedAt(LocalDateTime.now());
            fileMetadataRepository.save(metadata);
            return;
        }
        metadata.setSummary(summary);
        metadata.setKeywords(writeJson(limit(result.keywords())));
        metadata.setExtractedEntities(writeJson(normalizeEntities(result.entities())));
        metadata.setExtractionConfidence(clampConfidence(result.confidence()));
        String documentType = validateDocumentType(result.documentType());
        if ((metadata.getDocumentType() == null || metadata.getDocumentType().isBlank()) && documentType != null) {
            metadata.setDocumentType(documentType);
        }
        metadata.setExtractionStatus("READY");
        metadata.setUpdatedAt(LocalDateTime.now());
        fileMetadataRepository.save(metadata);
    }

    private void markFailed(Long fileId) {
        fileMetadataRepository.findById(fileId).ifPresent(metadata -> {
            if (!"GENERATING".equals(metadata.getExtractionStatus())) {
                return;
            }
            metadata.setExtractionStatus("FAILED");
            metadata.setUpdatedAt(LocalDateTime.now());
            fileMetadataRepository.save(metadata);
        });
    }

    private String truncate(String value, int max) {
        if (value == null) {
            return null;
        }
        String trimmed = value.trim();
        return trimmed.length() <= max ? trimmed : trimmed.substring(0, max);
    }

    private List<String> limit(List<String> values) {
        if (values == null) {
            return null;
        }
        return values.size() <= MAX_LIST_ITEMS ? values : values.subList(0, MAX_LIST_ITEMS);
    }

    private AutoMetadataResult.Entities normalizeEntities(AutoMetadataResult.Entities entities) {
        if (entities == null) {
            return null;
        }
        return new AutoMetadataResult.Entities(
                limit(entities.dates()),
                limit(entities.people()),
                limit(entities.amounts()),
                entities.project());
    }

    private Double clampConfidence(Double value) {
        if (value == null || !Double.isFinite(value)) {
            return null;
        }
        if (value < 0.0) {
            return 0.0;
        }
        if (value > 1.0) {
            return 1.0;
        }
        return value;
    }

    private String validateDocumentType(String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        String trimmed = value.trim();
        return metadataProperties.getDocumentTypes().contains(trimmed) ? trimmed : null;
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