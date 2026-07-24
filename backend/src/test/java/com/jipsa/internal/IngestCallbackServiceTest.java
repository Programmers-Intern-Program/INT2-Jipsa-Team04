package com.jipsa.internal;

import com.jipsa.chunk.ChunkSyncService;
import com.jipsa.file.File;
import com.jipsa.file.FileRepository;
import com.jipsa.file.FileStatus;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class IngestCallbackServiceTest {

    @Mock
    private FileRepository fileRepository;
    @Mock
    private ChunkSyncService chunkSyncService;

    @InjectMocks
    private IngestCallbackService ingestCallbackService;

    private File processingFile() {
        File file = new File();
        file.setStatus(FileStatus.PROCESSING);
        file.setProcessingStage("EMBEDDING");
        return file;
    }

    private IngestCompleteRequest.ChunkPayload chunk() {
        return new IngestCompleteRequest.ChunkPayload("id-0", 0, "content", "hash", null, Map.of());
    }

    @Test
    void staleCallbackDoesNotMarkFileReady() {
        File file = processingFile();
        when(fileRepository.findByIdAndDeletedAtIsNull(7L)).thenReturn(Optional.of(file));
        when(chunkSyncService.sync(anyLong(), any(), any())).thenReturn(ChunkSyncService.SyncOutcome.STALE);

        ingestCallbackService.complete(7L, new IngestCompleteRequest(true, null, 1, null, List.of(chunk())));

        assertThat(file.getStatus()).isEqualTo(FileStatus.PROCESSING);
        assertThat(file.getProcessingStage()).isEqualTo("EMBEDDING");
    }

    @Test
    void storedCallbackMarksFileReady() {
        File file = processingFile();
        when(fileRepository.findByIdAndDeletedAtIsNull(7L)).thenReturn(Optional.of(file));
        when(chunkSyncService.sync(anyLong(), any(), any())).thenReturn(ChunkSyncService.SyncOutcome.STORED);

        ingestCallbackService.complete(7L, new IngestCompleteRequest(true, null, 2, null, List.of(chunk())));

        assertThat(file.getStatus()).isEqualTo(FileStatus.READY);
        assertThat(file.getProcessingStage()).isNull();
    }

    @Test
    void successWithoutChunksFails() {
        File file = processingFile();
        when(fileRepository.findByIdAndDeletedAtIsNull(7L)).thenReturn(Optional.of(file));

        ingestCallbackService.complete(7L, new IngestCompleteRequest(true, null, 2, null, List.of()));

        assertThat(file.getStatus()).isEqualTo(FileStatus.FAILED);
    }
}