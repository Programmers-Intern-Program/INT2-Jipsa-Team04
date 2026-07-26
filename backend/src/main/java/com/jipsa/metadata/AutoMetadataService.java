package com.jipsa.metadata;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.jipsa.chunk.Chunk;
import com.jipsa.chunk.ChunkRepository;
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
import java.util.UUID;

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
        String token = UUID.randomUUID().toString();
        Integer claimed = transactionTemplate.execute(status ->
                fileMetadataRepository.claimForGeneration(fileId, token, LocalDateTime.now()));
        if (claimed == null || claimed == 0) {
            return;
        }
        String sample = buildSample(fileId);
        if (sample.isBlank()) {
            transactionTemplate.executeWithoutResult(status ->
                    fileMetadataRepository.failGeneration(fileId, token, LocalDateTime.now()));
            return;
        }
        AutoMetadataResult result;
        try {
            result = autoMetadataClient.generate(sample);
        } catch (RuntimeException e) {
            log.warn("AI 메타데이터 생성 실패 (file {}): {}", fileId, e.getMessage());
            transactionTemplate.executeWithoutResult(status ->
                    fileMetadataRepository.failGeneration(fileId, token, LocalDateTime.now()));
            return;
        }
        try {
            transactionTemplate.executeWithoutResult(status -> persist(fileId, token, result));
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

    private void persist(Long fileId, String token, AutoMetadataResult result) {
        String summary = truncate(result == null ? null : result.summary(), maxSummaryChars);
        if (summary == null || summary.isBlank()) {
            fileMetadataRepository.failGeneration(fileId, token, LocalDateTime.now());
            return;
        }
        int updated = fileMetadataRepository.completeGeneration(
                fileId, token, summary,
                writeJson(limit(result.keywords())),
                writeJson(normalizeEntities(result.entities())),
                clampConfidence(result.confidence()),
                validateDocumentType(result.documentType()),
                LocalDateTime.now());
        if (updated == 0) {
            log.info("AI 메타데이터 저장 건너뜀(다른 작업이 선점) file {}", fileId);
        }
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