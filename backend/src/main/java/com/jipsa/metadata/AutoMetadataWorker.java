package com.jipsa.metadata;

import com.jipsa.file.FileMetadataRepository;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.domain.PageRequest;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.List;

@Component
public class AutoMetadataWorker {

    private final FileMetadataRepository fileMetadataRepository;
    private final AutoMetadataService autoMetadataService;
    private final boolean enabled;
    private final int batchSize;

    public AutoMetadataWorker(FileMetadataRepository fileMetadataRepository,
                              AutoMetadataService autoMetadataService,
                              @Value("${app.metadata.ai.enabled:true}") boolean enabled,
                              @Value("${app.metadata.ai.batch-size:5}") int batchSize) {
        this.fileMetadataRepository = fileMetadataRepository;
        this.autoMetadataService = autoMetadataService;
        this.enabled = enabled;
        this.batchSize = batchSize;
    }

    @Scheduled(fixedDelayString = "${app.metadata.ai.poll-interval-ms:10000}")
    public void poll() {
        if (!enabled) {
            return;
        }
        List<Long> fileIds = fileMetadataRepository.findFileIdsPendingAiMetadata(PageRequest.of(0, batchSize));
        for (Long fileId : fileIds) {
            autoMetadataService.process(fileId);
        }
    }
}