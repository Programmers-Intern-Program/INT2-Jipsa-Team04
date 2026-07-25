package com.jipsa.metadata;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.jipsa.chunk.Chunk;
import com.jipsa.chunk.ChunkRepository;
import com.jipsa.file.File;
import com.jipsa.file.FileMetadata;
import com.jipsa.file.FileMetadataRepository;
import com.jipsa.file.FileRepository;
import com.jipsa.file.FileStatus;
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
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class AutoMetadataServiceTest {

    @Mock private FileRepository fileRepository;
    @Mock private FileMetadataRepository fileMetadataRepository;
    @Mock private ChunkRepository chunkRepository;
    @Mock private AutoMetadataClient autoMetadataClient;
    @Mock private PlatformTransactionManager transactionManager;

    private AutoMetadataService service;

    @BeforeEach
    void setUp() {
        lenient().when(transactionManager.getTransaction(any())).thenReturn(new SimpleTransactionStatus());
        service = new AutoMetadataService(fileRepository, fileMetadataRepository, chunkRepository,
                autoMetadataClient, new ObjectMapper(), transactionManager, 6, 4000);
    }

    private FileMetadata processingMetadata(String documentType) {
        FileMetadata metadata = new FileMetadata();
        metadata.setFileId(1L);
        metadata.setExtractionStatus("PROCESSING");
        metadata.setDocumentType(documentType);
        return metadata;
    }

    private void stubReadyFileWithChunk() {
        File file = mock(File.class);
        lenient().when(file.getStatus()).thenReturn(FileStatus.READY);
        when(fileRepository.findByIdAndDeletedAtIsNull(1L)).thenReturn(Optional.of(file));
        Chunk chunk = mock(Chunk.class);
        lenient().when(chunk.getContent()).thenReturn("문서 앞부분 본문입니다.");
        when(chunkRepository.findByFileIdOrderByChunkIndexAsc(eq(1L), any())).thenReturn(List.of(chunk));
    }

    @Test
    void generatesAndPersistsMetadata() {
        FileMetadata metadata = processingMetadata(null);
        when(fileMetadataRepository.findById(1L)).thenReturn(Optional.of(metadata));
        stubReadyFileWithChunk();
        when(autoMetadataClient.generate(any())).thenReturn(
                new AutoMetadataResult("요약입니다.", List.of("키워드"), List.of("엔티티"), "보고서", 0.9));

        service.process(1L);

        assertThat(metadata.getSummary()).isEqualTo("요약입니다.");
        assertThat(metadata.getExtractionConfidence()).isEqualTo(0.9);
        assertThat(metadata.getDocumentType()).isEqualTo("보고서");
        assertThat(metadata.getExtractionStatus()).isEqualTo("READY");
    }

    @Test
    void keepsUserDocumentType() {
        FileMetadata metadata = processingMetadata("계약서");
        when(fileMetadataRepository.findById(1L)).thenReturn(Optional.of(metadata));
        stubReadyFileWithChunk();
        when(autoMetadataClient.generate(any())).thenReturn(
                new AutoMetadataResult("요약", List.of("키워드"), List.of(), "보고서", 0.5));

        service.process(1L);

        assertThat(metadata.getDocumentType()).isEqualTo("계약서");
        assertThat(metadata.getExtractionStatus()).isEqualTo("READY");
    }

    @Test
    void clientFailureMarksFailed() {
        FileMetadata metadata = processingMetadata(null);
        when(fileMetadataRepository.findById(1L)).thenReturn(Optional.of(metadata));
        stubReadyFileWithChunk();
        when(autoMetadataClient.generate(any())).thenThrow(new RuntimeException("boom"));

        service.process(1L);

        assertThat(metadata.getExtractionStatus()).isEqualTo("FAILED");
    }
}