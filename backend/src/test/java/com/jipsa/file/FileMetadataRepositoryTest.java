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

    private void persistMetadata(Long fileId, String status, String documentType, String claimToken) {
        FileMetadata metadata = new FileMetadata();
        metadata.setFileId(fileId);
        metadata.setFileType("pdf");
        metadata.setExtractionStatus(status);
        metadata.setDocumentType(documentType);
        metadata.setClaimToken(claimToken);
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
    void claimSetsGeneratingTokenAndMaxVersion() {
        Long fileId = persistFile(FileStatus.READY);
        persistMetadata(fileId, "PROCESSING", null, null);
        persistChunk(fileId, 0, 1);
        persistChunk(fileId, 1, 2);

        int claimed = fileMetadataRepository.claimForGeneration(fileId, "tokA", LocalDateTime.now());

        assertThat(claimed).isEqualTo(1);
        FileMetadata reloaded = reload(fileId);
        assertThat(reloaded.getExtractionStatus()).isEqualTo("GENERATING");
        assertThat(reloaded.getClaimToken()).isEqualTo("tokA");
        assertThat(reloaded.getExtractionIndexVersion()).isEqualTo(2);
    }

    @Test
    void claimIsZeroWhenFileNotReady() {
        Long fileId = persistFile(FileStatus.PROCESSING);
        persistMetadata(fileId, "PROCESSING", null, null);

        assertThat(fileMetadataRepository.claimForGeneration(fileId, "tokA", LocalDateTime.now())).isZero();
        assertThat(reload(fileId).getExtractionStatus()).isEqualTo("PROCESSING");
    }

    @Test
    void completeAppliesWithMatchingToken() {
        Long fileId = persistFile(FileStatus.READY);
        persistMetadata(fileId, "GENERATING", null, "tokA");

        int updated = fileMetadataRepository.completeGeneration(
                fileId, "tokA", "요약", "[\"k\"]", null, 0.9, "보고서", LocalDateTime.now());

        assertThat(updated).isEqualTo(1);
        FileMetadata reloaded = reload(fileId);
        assertThat(reloaded.getExtractionStatus()).isEqualTo("READY");
        assertThat(reloaded.getSummary()).isEqualTo("요약");
    }

    @Test
    void completeRejectsWrongToken() {
        Long fileId = persistFile(FileStatus.READY);
        persistMetadata(fileId, "GENERATING", null, "tokB");

        int updated = fileMetadataRepository.completeGeneration(
                fileId, "tokA", "요약", "[\"k\"]", null, 0.9, "보고서", LocalDateTime.now());

        assertThat(updated).isZero();
        FileMetadata reloaded = reload(fileId);
        assertThat(reloaded.getExtractionStatus()).isEqualTo("GENERATING");
        assertThat(reloaded.getSummary()).isNull();
    }

    @Test
    void completeIsNoOpWhenProcessing() {
        Long fileId = persistFile(FileStatus.READY);
        persistMetadata(fileId, "PROCESSING", null, "tokA");

        int updated = fileMetadataRepository.completeGeneration(
                fileId, "tokA", "요약", "[\"k\"]", null, 0.9, "보고서", LocalDateTime.now());

        assertThat(updated).isZero();
        assertThat(reload(fileId).getExtractionStatus()).isEqualTo("PROCESSING");
    }

    @Test
    void completeKeepsUserDocumentType() {
        Long fileId = persistFile(FileStatus.READY);
        persistMetadata(fileId, "GENERATING", "계약서", "tokA");

        fileMetadataRepository.completeGeneration(
                fileId, "tokA", "요약", null, null, 0.5, "보고서", LocalDateTime.now());

        assertThat(reload(fileId).getDocumentType()).isEqualTo("계약서");
    }

    @Test
    void failRejectsWrongToken() {
        Long fileId = persistFile(FileStatus.READY);
        persistMetadata(fileId, "GENERATING", null, "tokB");

        assertThat(fileMetadataRepository.failGeneration(fileId, "tokA", LocalDateTime.now())).isZero();
        assertThat(reload(fileId).getExtractionStatus()).isEqualTo("GENERATING");
    }

    @Test
    void reaperResetsStaleAndClearsToken() {
        Long fileId = persistFile(FileStatus.READY);
        persistMetadata(fileId, "GENERATING", null, "tokA");

        int reset = fileMetadataRepository.resetStaleGenerating(
                LocalDateTime.now().plusMinutes(1), LocalDateTime.now());

        assertThat(reset).isEqualTo(1);
        FileMetadata reloaded = reload(fileId);
        assertThat(reloaded.getExtractionStatus()).isEqualTo("PROCESSING");
        assertThat(reloaded.getClaimToken()).isNull();
    }

    @Test
    void pendingListsReadyProcessingAndSkipMarksThem() {
        Long ready = persistFile(FileStatus.READY);
        persistMetadata(ready, "PROCESSING", null, null);
        Long notReady = persistFile(FileStatus.PROCESSING);
        persistMetadata(notReady, "PROCESSING", null, null);

        List<Long> pending = fileMetadataRepository.findFileIdsPendingAiMetadata(PageRequest.of(0, 10));
        assertThat(pending).contains(ready).doesNotContain(notReady);

        assertThat(fileMetadataRepository.markPendingSkipped(LocalDateTime.now())).isEqualTo(1);
        assertThat(reload(ready).getExtractionStatus()).isEqualTo("SKIPPED");
        assertThat(reload(notReady).getExtractionStatus()).isEqualTo("PROCESSING");
    }
}