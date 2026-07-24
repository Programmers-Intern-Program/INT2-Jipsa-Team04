package com.jipsa.chunk;

import com.jipsa.internal.IngestCompleteRequest.ChunkPayload;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class ChunkSyncServiceTest {

    @Mock
    private ChunkRepository chunkRepository;

    @InjectMocks
    private ChunkSyncService chunkSyncService;

    @Test
    void persistsChunksReplacingExisting() {
        List<ChunkPayload> chunks = List.of(
                new ChunkPayload("id-0", 0, "content-0", "hash", 10, Map.of("page_number", 3)),
                new ChunkPayload("id-1", 1, "content-1", "hash", 20, Map.of()));
        when(chunkRepository.findMaxIndexVersionByFileId(5L)).thenReturn(null);

        chunkSyncService.sync(5L, 2, chunks);

        verify(chunkRepository).deleteByFileId(5L);
        @SuppressWarnings("unchecked")
        ArgumentCaptor<List<Chunk>> captor = ArgumentCaptor.forClass(List.class);
        verify(chunkRepository).saveAll(captor.capture());
        List<Chunk> saved = captor.getValue();
        assertThat(saved).hasSize(2);
        assertThat(saved.get(0).getChunkId()).isEqualTo("id-0");
        assertThat(saved.get(0).getPage()).isEqualTo(3);
        assertThat(saved.get(0).getIndexVersion()).isEqualTo(2);
        assertThat(saved.get(1).getPage()).isNull();
    }

    @Test
    void skipsStaleIndexVersion() {
        when(chunkRepository.findMaxIndexVersionByFileId(5L)).thenReturn(3);

        chunkSyncService.sync(5L, 2, List.of(
                new ChunkPayload("id-0", 0, "content-0", "hash", null, Map.of())));

        verify(chunkRepository, never()).deleteByFileId(5L);
        verify(chunkRepository, never()).saveAll(any());
    }

    @Test
    void skipsWhenNoChunks() {
        chunkSyncService.sync(5L, 2, List.of());

        verify(chunkRepository, never()).deleteByFileId(any());
        verify(chunkRepository, never()).saveAll(any());
    }
}