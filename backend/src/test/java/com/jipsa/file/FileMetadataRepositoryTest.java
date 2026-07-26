package com.jipsa.file;

import com.jipsa.chunk.Chunk;
import com.jipsa.chunk.ChunkRepository;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.data.jpa.test.autoconfigure.DataJpaTest;
import org.springframework.data.domain.PageRequest;

import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

@DataJpaTest
class FileMetadataRepositoryTest {

    @Autowired private FileRepository fileRepository;
    @Autowired private FileMetadataRepository fileMetadataRepository;
    @Autowired private ChunkRepository chunkRepository;

    private Long persistFile(FileStatus status) {
        File file = new File();
        file.setUsersId(7L);
        file.setName("doc.pdf");
        file.setS3Key("s3-" + UUID.randomUUID());
        file.setFileType("pdf");
        file.setStatus(status);
        return fileRepository.saveAndFlush(file).getId();
    }

    private void persistMetadata(Long fileId, String extractionStatus, String documentType) {
        FileMetadata metadata = new FileMetadata();
        metadata.setFileId(fileId);
        metadata.setFileType("pdf");
        metadata.setExtractionStatus(extractionStatus);
        metadata.setDocumentType(documentType);
        fileMetadataRepository.saveAndFlush(metadata);
    }

    private void persistChunk(Long fileId, int index, int version) {
        Chunk chunk = new Chunk();
        chunk.setChunkId("chunk-" + fileId + "-" + index + "-" + version);
        chunk.setFileId(fileId);
        chunk.setChunkIndex(index);
        chunk.setContent("본문 " + index);
        chunk.setIndexVersion(version);
        chunkRepository.saveAndFlush(chunk);
    }

    private FileMetadata reload(Long fileId) {
        return fileMetadataRepository.findById(fileId).orElseThrow();
    }

    @Test
    void claimFlipsProcessingToGeneratingAndSnapshotsMaxVersion() {
        Long fileId = persistFile(FileStatus.READY);
        persistMetadata(fileId, "PROCESSING", null);
        persistChunk(fileId, 0, 1);
        persistChunk(fileId, 1, 2);

        int claimed = fileMetadataRepository.claimForGeneration(fileId, LocalDateTime.now());

        assertThat(claimed).isEqualTo(1);
        FileMetadata reloaded = reload(fileId);
        assertThat(reloaded.getExtractionStatus()).isEqualTo("GENERATING");
        assertThat(reloaded.getExtractionIndexVersion()).isEqualTo(2);
    }

    @Test
    void claimIsZeroWhenFileNotReady() {
        Long fileId = persistFile(FileStatus.PROCESSING);
        persistMetadata(fileId, "PROCESSING", null);

        int claimed = fileMetadataRepository.claimForGeneration(fileId, LocalDateTime.now());

        assertThat(claimed).isZero();
        assertThat(reload(fileId).getExtractionStatus()).isEqualTo("PROCESSING");
    }

    @Test
    void completeAppliesWhenGenerating() {
        Long fileId = persistFile(FileStatus.READY);
        persistMetadata(fileId, "GENERATING", null);

        int updated = fileMetadataRepository.completeGeneration(
                fileId, "요약", "[\"k\"]", null, 0.9, "보고서", LocalDateTime.now());

        assertThat(updated).isEqualTo(1);
        FileMetadata reloaded = reload(fileId);
        assertThat(reloaded.getExtractionStatus()).isEqualTo("READY");
        assertThat(reloaded.getSummary()).isEqualTo("요약");
        assertThat(reloaded.getDocumentType()).isEqualTo("보고서");
    }

    @Test
    void completeIsNoOpWhenProcessing() {
        Long fileId = persistFile(FileStatus.READY);
        persistMetadata(fileId, "PROCESSING", null);

        int updated = fileMetadataRepository.completeGeneration(
                fileId, "요약", "[\"k\"]", null, 0.9, "보고서", LocalDateTime.now());

        assertThat(updated).isZero();
        FileMetadata reloaded = reload(fileId);
        assertThat(reloaded.getExtractionStatus()).isEqualTo("PROCESSING");
        assertThat(reloaded.getSummary()).isNull();
    }

    @Test
    void completeKeepsUserDocumentType() {
        Long fileId = persistFile(FileStatus.READY);
        persistMetadata(fileId, "GENERATING", "계약서");

        fileMetadataRepository.completeGeneration(
                fileId, "요약", null, null, 0.5, "보고서", LocalDateTime.now());

        assertThat(reload(fileId).getDocumentType()).isEqualTo("계약서");
    }

    @Test
    void failIsNoOpWhenNotGenerating() {
        Long fileId = persistFile(FileStatus.READY);
        persistMetadata(fileId, "PROCESSING", null);

        int updated = fileMetadataRepository.failGeneration(fileId, LocalDateTime.now());

        assertThat(updated).isZero();
        assertThat(reload(fileId).getExtractionStatus()).isEqualTo("PROCESSING");
    }

    @Test
    void pendingListsReadyProcessingAndSkipMarksThem() {
        Long ready = persistFile(FileStatus.READY);
        persistMetadata(ready, "PROCESSING", null);
        Long notReady = persistFile(FileStatus.PROCESSING);
        persistMetadata(notReady, "PROCESSING", null);

        List<Long> pending = fileMetadataRepository.findFileIdsPendingAiMetadata(PageRequest.of(0, 10));
        assertThat(pending).contains(ready).doesNotContain(notReady);

        int skipped = fileMetadataRepository.markPendingSkipped(LocalDateTime.now());
        assertThat(skipped).isEqualTo(1);
        assertThat(reload(ready).getExtractionStatus()).isEqualTo("SKIPPED");
        assertThat(reload(notReady).getExtractionStatus()).isEqualTo("PROCESSING");
    }
}