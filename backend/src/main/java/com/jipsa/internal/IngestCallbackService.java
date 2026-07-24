package com.jipsa.internal;

import com.jipsa.chunk.ChunkSyncService;
import com.jipsa.common.exception.FileNotFoundException;
import com.jipsa.file.File;
import com.jipsa.file.FileRepository;
import com.jipsa.file.FileStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class IngestCallbackService {

    private final FileRepository fileRepository;
    private final ChunkSyncService chunkSyncService;

    public IngestCallbackService(FileRepository fileRepository,
                                 ChunkSyncService chunkSyncService) {
        this.fileRepository = fileRepository;
        this.chunkSyncService = chunkSyncService;
    }

    @Transactional
    public void complete(Long fileIdx, IngestCompleteRequest request) {
        File file = fileRepository.findByIdAndDeletedAtIsNull(fileIdx)
                .orElseThrow(() -> new FileNotFoundException("파일을 찾을 수 없습니다: " + fileIdx));
        if (request.success()) {
            file.setStatus(FileStatus.READY);
            file.setErrorMessage(null);
            chunkSyncService.sync(fileIdx, request.indexVersion(), request.chunks());
        } else {
            file.setStatus(FileStatus.FAILED);
            file.setErrorMessage(request.errorMessage());
        }
        file.setProcessingStage(null);
    }
}