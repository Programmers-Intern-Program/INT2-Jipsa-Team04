package com.jipsa.file;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.data.jpa.test.autoconfigure.DataJpaTest;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionTemplate;

import java.time.LocalDateTime;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;

@DataJpaTest
@Transactional(propagation = Propagation.NOT_SUPPORTED)
class FileMetadataConcurrencyTest {

    @Autowired private FileRepository fileRepository;
    @Autowired private FileMetadataRepository fileMetadataRepository;
    @Autowired private PlatformTransactionManager transactionManager;

    @Test
    void concurrentCallbackAndTagEditPreserveBothResults() throws Exception {
        TransactionTemplate transaction = new TransactionTemplate(transactionManager);
        Long fileId = transaction.execute(status -> {
            File file = new File();
            file.setUsersId(7L);
            file.setName("doc.pdf");
            file.setS3Key("s3-" + UUID.randomUUID());
            file.setFileType("pdf");
            file.setStatus(FileStatus.READY);
            Long savedFileId = fileRepository.saveAndFlush(file).getId();
            FileMetadata metadata = new FileMetadata();
            metadata.setFileId(savedFileId);
            metadata.setFileType("pdf");
            metadata.setTags("[\"기존\"]");
            metadata.setKeywords("[\"이전키워드\"]");
            metadata.setExtractionStatus("READY");
            metadata.setExtractionIndexVersion(1);
            fileMetadataRepository.saveAndFlush(metadata);
            return savedFileId;
        });

        CountDownLatch ready = new CountDownLatch(2);
        CountDownLatch start = new CountDownLatch(1);
        var executor = Executors.newFixedThreadPool(2);
        try {
            var callback = executor.submit(() -> transaction.executeWithoutResult(status -> {
                ready.countDown();
                await(start);
                fileMetadataRepository.applyCallbackSuccess(
                        fileId, "새 요약", "[\"새키워드\"]", null, 0.9, 2, LocalDateTime.now());
            }));
            var tagEdit = executor.submit(() -> transaction.executeWithoutResult(status -> {
                ready.countDown();
                await(start);
                fileMetadataRepository.updateTags(fileId, "[\"새태그\"]", LocalDateTime.now());
            }));

            assertThat(ready.await(5, TimeUnit.SECONDS)).isTrue();
            start.countDown();
            callback.get(5, TimeUnit.SECONDS);
            tagEdit.get(5, TimeUnit.SECONDS);
        } finally {
            executor.shutdownNow();
        }

        FileMetadata result = transaction.execute(status -> fileMetadataRepository.findById(fileId).orElseThrow());
        assertThat(result.getTags()).isEqualTo("[\"새태그\"]");
        assertThat(result.getKeywords()).isEqualTo("[\"새키워드\"]");
        assertThat(result.getSummary()).isEqualTo("새 요약");
        assertThat(result.getExtractionIndexVersion()).isEqualTo(2);

        transaction.executeWithoutResult(status -> {
            fileMetadataRepository.deleteById(fileId);
            fileRepository.deleteById(fileId);
        });
    }

    private static void await(CountDownLatch latch) {
        try {
            latch.await();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException(e);
        }
    }
}
