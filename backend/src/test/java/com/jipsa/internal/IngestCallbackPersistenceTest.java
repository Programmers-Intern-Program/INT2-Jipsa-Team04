package com.jipsa.internal;

import com.jipsa.chunk.Chunk;
import com.jipsa.chunk.ChunkRepository;
import com.jipsa.chunk.ChunkSyncService;
import com.jipsa.file.File;
import com.jipsa.file.FileRepository;
import com.jipsa.file.FileStatus;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.data.jpa.test.autoconfigure.DataJpaTest;
import org.springframework.context.annotation.Import;

import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

@DataJpaTest
@Import({IngestCallbackService.class, ChunkSyncService.class})
class IngestCallbackPersistenceTest {

    @Autowired
    private IngestCallbackService ingestCallbackService;
    @Autowired
    private FileRepository fileRepository;
    @Autowired
    private ChunkRepository chunkRepository;

    @Test
    void storedCallbackPersistsReadyAfterChunkBulkDeleteClearsContext() {
        File file = new File();
        file.setUsersId(7L);
        file.setName("meeting.docx");
        file.setS3Key("s3-" + UUID.randomUUID());
        file.setFileType("docx");
        file.setStatus(FileStatus.PROCESSING);
        file.setProcessingStage("EMBEDDING");
        Long fileId = fileRepository.saveAndFlush(file).getId();

        Chunk previous = new Chunk();
        previous.setChunkId("old-chunk");
        previous.setFileId(fileId);
        previous.setChunkIndex(0);
        previous.setContent("old");
        previous.setIndexVersion(1);
        chunkRepository.saveAndFlush(previous);

        IngestCompleteRequest request = new IngestCompleteRequest(
                true,
                null,
                2,
                1,
                List.of(new IngestCompleteRequest.ChunkPayload(
                        "new-chunk",
                        0,
                        "new",
                        "hash",
                        1,
                        Map.of("page_number", 1))));

        ingestCallbackService.complete(fileId, request);

        File reloaded = fileRepository.findById(fileId).orElseThrow();
        assertThat(reloaded.getStatus()).isEqualTo(FileStatus.READY);
        assertThat(reloaded.getProcessingStage()).isNull();
        assertThat(chunkRepository.findMaxIndexVersionByFileId(fileId)).isEqualTo(2);
        assertThat(chunkRepository.countByFileId(fileId)).isEqualTo(1);
    }
}
