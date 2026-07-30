package com.jipsa.internal;

import com.jipsa.file.File;
import com.jipsa.file.FileMetadataRepository;
import com.jipsa.file.FileRepository;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;
import java.util.Optional;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
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
        when(fileRepository.findByIdAndDeletedAtIsNull(3L)).thenReturn(Optional.of(file()));
        when(fileMetadataRepository.applyCallbackSuccess(
                eq(3L), any(), any(), any(), any(), eq(4), any())).thenReturn(1);

        IngestMetadataRequest.Entities entities =
                new IngestMetadataRequest.Entities(List.of("2026-07-24"), List.of("김철수"), List.of("1,000원"), "프로젝트A");
        metadataCallbackService.apply(3L,
                new IngestMetadataRequest(true, null, 4, "요약본", List.of("kw1", "kw2"), 0.87, entities));

        verify(fileMetadataRepository).applyCallbackSuccess(
                eq(3L), eq("요약본"), eq("[\"kw1\",\"kw2\"]"),
                any(), eq(0.87), eq(4), any());
        verify(fileMetadataRepository, never()).save(any());
    }

    @Test
    void failureMarksExtractionFailed() {
        when(fileRepository.findByIdAndDeletedAtIsNull(3L)).thenReturn(Optional.of(file()));
        when(fileMetadataRepository.applyCallbackFailure(eq(3L), eq(4), any())).thenReturn(1);

        metadataCallbackService.apply(3L,
                new IngestMetadataRequest(false, "extraction failed", 4, null, null, null, null));

        verify(fileMetadataRepository).applyCallbackFailure(eq(3L), eq(4), any());
        verify(fileMetadataRepository, never()).save(any());
    }

    @Test
    void staleCallbackIsIgnored() {
        when(fileRepository.findByIdAndDeletedAtIsNull(3L)).thenReturn(Optional.of(file()));
        when(fileMetadataRepository.applyCallbackSuccess(
                eq(3L), any(), any(), any(), any(), eq(3), any())).thenReturn(0);
        when(fileMetadataRepository.existsById(3L)).thenReturn(true);

        metadataCallbackService.apply(3L,
                new IngestMetadataRequest(true, null, 3, "오래된요약", List.of(), 0.5, null));

        verify(fileMetadataRepository, never()).save(any());
    }
}
