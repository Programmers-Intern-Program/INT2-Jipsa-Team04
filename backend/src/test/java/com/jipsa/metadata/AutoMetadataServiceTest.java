package com.jipsa.metadata;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.jipsa.chunk.Chunk;
import com.jipsa.chunk.ChunkRepository;
import com.jipsa.file.FileDetailResponse;
import com.jipsa.file.FileMetadata;
import com.jipsa.file.FileMetadataRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.SimpleTransactionStatus;

import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class AutoMetadataServiceTest {

    @Mock private FileMetadataRepository fileMetadataRepository;
    @Mock private ChunkRepository chunkRepository;
    @Mock private AutoMetadataClient autoMetadataClient;
    @Mock private PlatformTransactionManager transactionManager;

    private final ObjectMapper objectMapper = new ObjectMapper();
    private AutoMetadataService service;

    @BeforeEach
    void setUp() {
        lenient().when(transactionManager.getTransaction(any())).thenReturn(new SimpleTransactionStatus());
        MetadataProperties properties = new MetadataProperties();
        properties.setDocumentTypes(List.of("계약서", "보고서", "청구서"));
        service = new AutoMetadataService(fileMetadataRepository, chunkRepository, autoMetadataClient,
                objectMapper, properties, transactionManager, 6, 4000, 300000L, 500);
    }

    private FileMetadata generatingMetadata(String documentType, Integer indexVersion) {
        FileMetadata metadata = new FileMetadata();
        metadata.setFileId(1L);
        metadata.setExtractionStatus("GENERATING");
        metadata.setExtractionIndexVersion(indexVersion);
        metadata.setDocumentType(documentType);
        return metadata;
    }

    private void stubClaim(int version) {
        when(chunkRepository.findMaxIndexVersionByFileId(1L)).thenReturn(version);
        when(fileMetadataRepository.claimForGeneration(eq(1L), any(), any())).thenReturn(1);
        Chunk chunk = mock(Chunk.class);
        lenient().when(chunk.getContent()).thenReturn("문서 앞부분 본문입니다.");
        lenient().when(chunkRepository.findByFileIdOrderByChunkIndexAsc(eq(1L), any())).thenReturn(List.of(chunk));
    }

    private AutoMetadataResult result(String summary, List<String> keywords, String documentType, Double confidence) {
        return new AutoMetadataResult(summary, keywords,
                new AutoMetadataResult.Entities(List.of("2026-01-01"), List.of("김철수"), List.of("100만원"), null),
                documentType, confidence);
    }

    @Test
    void generatesAndPersistsMetadata() {
        stubClaim(1);
        when(fileMetadataRepository.findById(1L)).thenReturn(Optional.of(generatingMetadata(null, 1)));
        when(autoMetadataClient.generate(any())).thenReturn(result("요약입니다.", List.of("키워드"), "보고서", 0.9));

        service.process(1L);

        FileMetadata metadata = fileMetadataRepository.findById(1L).orElseThrow();
        assertThat(metadata.getSummary()).isEqualTo("요약입니다.");
        assertThat(metadata.getExtractionConfidence()).isEqualTo(0.9);
        assertThat(metadata.getDocumentType()).isEqualTo("보고서");
        assertThat(metadata.getExtractionStatus()).isEqualTo("READY");
    }

    @Test
    void entitiesRoundTripToDetailShape() throws Exception {
        stubClaim(1);
        when(fileMetadataRepository.findById(1L)).thenReturn(Optional.of(generatingMetadata(null, 1)));
        when(autoMetadataClient.generate(any())).thenReturn(result("요약", List.of("k"), "보고서", 0.8));

        service.process(1L);

        String json = fileMetadataRepository.findById(1L).orElseThrow().getExtractedEntities();
        FileDetailResponse.Entities parsed = objectMapper.readValue(json, FileDetailResponse.Entities.class);
        assertThat(parsed.dates()).containsExactly("2026-01-01");
        assertThat(parsed.people()).containsExactly("김철수");
        assertThat(parsed.amounts()).containsExactly("100만원");
    }

    @Test
    void keepsUserDocumentType() {
        stubClaim(1);
        when(fileMetadataRepository.findById(1L)).thenReturn(Optional.of(generatingMetadata("계약서", 1)));
        when(autoMetadataClient.generate(any())).thenReturn(result("요약", List.of("k"), "보고서", 0.5));

        service.process(1L);

        assertThat(fileMetadataRepository.findById(1L).orElseThrow().getDocumentType()).isEqualTo("계약서");
    }

    @Test
    void rejectsUnknownDocumentType() {
        stubClaim(1);
        when(fileMetadataRepository.findById(1L)).thenReturn(Optional.of(generatingMetadata(null, 1)));
        when(autoMetadataClient.generate(any())).thenReturn(result("요약", List.of("k"), "존재하지않는분류", 0.5));

        service.process(1L);

        FileMetadata metadata = fileMetadataRepository.findById(1L).orElseThrow();
        assertThat(metadata.getDocumentType()).isNull();
        assertThat(metadata.getExtractionStatus()).isEqualTo("READY");
    }

    @Test
    void clampsConfidence() {
        stubClaim(1);
        when(fileMetadataRepository.findById(1L)).thenReturn(Optional.of(generatingMetadata(null, 1)));
        when(autoMetadataClient.generate(any())).thenReturn(result("요약", List.of("k"), "보고서", 1.7));

        service.process(1L);

        assertThat(fileMetadataRepository.findById(1L).orElseThrow().getExtractionConfidence()).isEqualTo(1.0);
    }

    @Test
    void limitsKeywordsToFive() throws Exception {
        stubClaim(1);
        when(fileMetadataRepository.findById(1L)).thenReturn(Optional.of(generatingMetadata(null, 1)));
        when(autoMetadataClient.generate(any())).thenReturn(
                result("요약", List.of("1", "2", "3", "4", "5", "6", "7"), "보고서", 0.5));

        service.process(1L);

        String json = fileMetadataRepository.findById(1L).orElseThrow().getKeywords();
        assertThat(objectMapper.readValue(json, List.class)).hasSize(5);
    }

    @Test
    void blankSummaryMarksFailed() {
        stubClaim(1);
        when(fileMetadataRepository.findById(1L)).thenReturn(Optional.of(generatingMetadata(null, 1)));
        when(autoMetadataClient.generate(any())).thenReturn(result("   ", List.of("k"), "보고서", 0.5));

        service.process(1L);

        FileMetadata metadata = fileMetadataRepository.findById(1L).orElseThrow();
        assertThat(metadata.getSummary()).isNull();
        assertThat(metadata.getExtractionStatus()).isEqualTo("FAILED");
    }

    @Test
    void clientFailureMarksFailed() {
        stubClaim(1);
        when(fileMetadataRepository.findById(1L)).thenReturn(Optional.of(generatingMetadata(null, 1)));
        when(autoMetadataClient.generate(any())).thenThrow(new RuntimeException("boom"));

        service.process(1L);

        assertThat(fileMetadataRepository.findById(1L).orElseThrow().getExtractionStatus()).isEqualTo("FAILED");
    }

    @Test
    void skipsWhenClaimNotWon() {
        when(chunkRepository.findMaxIndexVersionByFileId(1L)).thenReturn(1);
        when(fileMetadataRepository.claimForGeneration(eq(1L), any(), any())).thenReturn(0);

        service.process(1L);

        verify(autoMetadataClient, never()).generate(any());
    }

    @Test
    void discardsStaleResult() {
        stubClaim(2);
        when(fileMetadataRepository.findById(1L)).thenReturn(Optional.of(generatingMetadata(null, 1)));
        when(autoMetadataClient.generate(any())).thenReturn(result("오래된 요약", List.of("k"), "보고서", 0.9));

        service.process(1L);

        FileMetadata metadata = fileMetadataRepository.findById(1L).orElseThrow();
        assertThat(metadata.getSummary()).isNull();
        assertThat(metadata.getExtractionStatus()).isEqualTo("GENERATING");
    }
}