package com.jipsa.metadata;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.jipsa.chunk.Chunk;
import com.jipsa.chunk.ChunkRepository;
import com.jipsa.file.FileDetailResponse;
import com.jipsa.file.FileMetadataRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.SimpleTransactionStatus;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
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

    private void stubClaim() {
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
    void completesWithValidatedValues() {
        stubClaim();
        when(autoMetadataClient.generate(any())).thenReturn(result("요약입니다.", List.of("키워드"), "보고서", 0.9));

        service.process(1L);

        verify(fileMetadataRepository).completeGeneration(eq(1L), any(), eq("요약입니다."), any(), any(), eq(0.9), eq("보고서"), any());
    }

    @Test
    void usesSameTokenForClaimAndComplete() {
        stubClaim();
        when(autoMetadataClient.generate(any())).thenReturn(result("요약", List.of("k"), "보고서", 0.9));
        ArgumentCaptor<String> claimToken = ArgumentCaptor.forClass(String.class);
        ArgumentCaptor<String> completeToken = ArgumentCaptor.forClass(String.class);

        service.process(1L);

        verify(fileMetadataRepository).claimForGeneration(eq(1L), claimToken.capture(), any());
        verify(fileMetadataRepository).completeGeneration(eq(1L), completeToken.capture(), any(), any(), any(), any(), any(), any());
        assertThat(completeToken.getValue()).isEqualTo(claimToken.getValue());
    }

    @Test
    void entitiesSerializedToDetailShape() throws Exception {
        stubClaim();
        when(autoMetadataClient.generate(any())).thenReturn(result("요약", List.of("k"), "보고서", 0.8));
        ArgumentCaptor<String> entities = ArgumentCaptor.forClass(String.class);

        service.process(1L);

        verify(fileMetadataRepository).completeGeneration(eq(1L), any(), any(), any(), entities.capture(), any(), any(), any());
        FileDetailResponse.Entities parsed = objectMapper.readValue(entities.getValue(), FileDetailResponse.Entities.class);
        assertThat(parsed.dates()).containsExactly("2026-01-01");
        assertThat(parsed.people()).containsExactly("김철수");
        assertThat(parsed.amounts()).containsExactly("100만원");
    }

    @Test
    void rejectsUnknownDocumentType() {
        stubClaim();
        when(autoMetadataClient.generate(any())).thenReturn(result("요약", List.of("k"), "존재하지않는분류", 0.5));

        service.process(1L);

        verify(fileMetadataRepository).completeGeneration(eq(1L), any(), any(), any(), any(), any(), isNull(), any());
    }

    @Test
    void clampsConfidence() {
        stubClaim();
        when(autoMetadataClient.generate(any())).thenReturn(result("요약", List.of("k"), "보고서", 1.7));

        service.process(1L);

        verify(fileMetadataRepository).completeGeneration(eq(1L), any(), any(), any(), any(), eq(1.0), any(), any());
    }

    @Test
    void limitsKeywordsToFive() throws Exception {
        stubClaim();
        when(autoMetadataClient.generate(any())).thenReturn(
                result("요약", List.of("1", "2", "3", "4", "5", "6", "7"), "보고서", 0.5));
        ArgumentCaptor<String> keywords = ArgumentCaptor.forClass(String.class);

        service.process(1L);

        verify(fileMetadataRepository).completeGeneration(eq(1L), any(), any(), keywords.capture(), any(), any(), any(), any());
        assertThat(objectMapper.readValue(keywords.getValue(), List.class)).hasSize(5);
    }

    @Test
    void blankSummaryFails() {
        stubClaim();
        when(autoMetadataClient.generate(any())).thenReturn(result("   ", List.of("k"), "보고서", 0.5));

        service.process(1L);

        verify(fileMetadataRepository).failGeneration(eq(1L), any(), any());
        verify(fileMetadataRepository, never()).completeGeneration(any(), any(), any(), any(), any(), any(), any(), any());
    }

    @Test
    void clientFailureFails() {
        stubClaim();
        when(autoMetadataClient.generate(any())).thenThrow(new RuntimeException("boom"));

        service.process(1L);

        verify(fileMetadataRepository).failGeneration(eq(1L), any(), any());
    }

    @Test
    void skipsWhenClaimNotWon() {
        when(fileMetadataRepository.claimForGeneration(eq(1L), any(), any())).thenReturn(0);

        service.process(1L);

        verify(autoMetadataClient, never()).generate(any());
        verify(fileMetadataRepository, never()).completeGeneration(any(), any(), any(), any(), any(), any(), any(), any());
    }
}