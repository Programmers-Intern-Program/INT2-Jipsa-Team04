package com.jipsa.chunk;

import com.jipsa.internal.IngestCompleteRequest;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.Map;

@Service
public class ChunkSyncService {

    private final ChunkRepository chunkRepository;

    public ChunkSyncService(ChunkRepository chunkRepository) {
        this.chunkRepository = chunkRepository;
    }

    public void sync(Long fileId, Integer indexVersion, List<IngestCompleteRequest.ChunkPayload> chunks) {
        if (indexVersion == null || chunks == null || chunks.isEmpty()) {
            return;
        }
        Integer existingVersion = chunkRepository.findMaxIndexVersionByFileId(fileId);
        if (existingVersion != null && indexVersion < existingVersion) {
            return;
        }
        chunkRepository.deleteByFileId(fileId);
        List<Chunk> entities = chunks.stream()
                .map(payload -> toEntity(fileId, indexVersion, payload))
                .toList();
        chunkRepository.saveAll(entities);
    }

    private Chunk toEntity(Long fileId, Integer indexVersion, IngestCompleteRequest.ChunkPayload payload) {
        Chunk chunk = new Chunk();
        chunk.setChunkId(payload.chunkId());
        chunk.setFileId(fileId);
        chunk.setChunkIndex(payload.chunkIndex());
        chunk.setContent(payload.content());
        chunk.setPage(extractPage(payload.sourceMetadata()));
        chunk.setIndexVersion(indexVersion);
        return chunk;
    }

    private Integer extractPage(Map<String, Object> sourceMetadata) {
        if (sourceMetadata == null) {
            return null;
        }
        Object page = sourceMetadata.get("page_number");
        if (page instanceof Number number) {
            return number.intValue();
        }
        return null;
    }
}