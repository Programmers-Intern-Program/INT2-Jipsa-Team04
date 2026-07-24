package com.jipsa.internal;

import com.jipsa.file.File;
import com.jipsa.file.FileMetadata;
import com.jipsa.file.FileMetadataRepository;
import com.jipsa.file.FileRepository;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class MetadataCallbackServiceTest {

    @Mock
    private FileRepository fileRepository;
    @Mock
    private FileMetadataRepository fileMetadataRepository;

    @InjectMocks
    private MetadataCallbackService metadataCallbackService;

    private File file() {
        File file = new File();
        file.setId(3L);
        file.setFileType("pdf");
        return file;
    }

    @Test
    void successPersistsAiFieldsAndPreservesUserEdits() {
        FileMetadata existing = new FileMetadata();
        existing.setFileId(3L);
        existing.setTags("[\"내태그\"]");
        existing.setDocumentType("계약서");
        when(fileRepository.findByIdAndDeletedAtIsNull(3L)).thenReturn(Optional.of(file()));
        when(fileMetadataRepository.findById(3L)).thenReturn(Optional.of(existing));

        IngestMetadataRequest.Entities entities =
                new IngestMetadataRequest.Entities(List.of("2026-07-24"), List.of("김철수"), List.of("1,000원"), "프로젝트A");
        metadataCallbackService.apply(3L,
                new IngestMetadataRequest(true, null, "요약본", List.of("kw1", "kw2"), 0.87, entities));

        ArgumentCaptor<FileMetadata> captor = ArgumentCaptor.forClass(FileMetadata.class);
        verify(fileMetadataRepository).save(captor.capture());
        FileMetadata saved = captor.getValue();
        assertThat(saved.getSummary()).isEqualTo("요약본");
        assertThat(saved.getExtractionStatus()).isEqualTo("READY");
        assertThat(saved.getExtractionConfidence()).isEqualTo(0.87);
        assertThat(saved.getExtractedEntities()).contains("김철수").contains("프로젝트A");
        assertThat(saved.getKeywords()).contains("kw1");
        assertThat(saved.getTags()).isEqualTo("[\"내태그\"]");
        assertThat(saved.getDocumentType()).isEqualTo("계약서");
    }

    @Test
    void failureMarksExtractionFailed() {
        when(fileRepository.findByIdAndDeletedAtIsNull(3L)).thenReturn(Optional.of(file()));
        when(fileMetadataRepository.findById(3L)).thenReturn(Optional.empty());

        metadataCallbackService.apply(3L,
                new IngestMetadataRequest(false, "extraction failed", null, null, null, null));

        ArgumentCaptor<FileMetadata> captor = ArgumentCaptor.forClass(FileMetadata.class);
        verify(fileMetadataRepository).save(captor.capture());
        assertThat(captor.getValue().getExtractionStatus()).isEqualTo("FAILED");
        assertThat(captor.getValue().getSummary()).isNull();
    }
}