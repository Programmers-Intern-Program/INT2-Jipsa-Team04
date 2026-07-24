package com.jipsa.internal;

import com.jipsa.chunk.ChunkSyncService;
import com.jipsa.common.exception.FileNotFoundException;
import com.jipsa.file.File;
import com.jipsa.file.FileRepository;
import com.jipsa.file.FileStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

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
            String inconsistency = validateSuccessPayload(request);
            if (inconsistency != null) {
                file.setStatus(FileStatus.FAILED);
                file.setErrorMessage(inconsistency);
                file.setProcessingStage(null);
                return;
            }
            ChunkSyncService.SyncOutcome outcome =
                    chunkSyncService.sync(fileIdx, request.indexVersion(), request.chunks());
            if (outcome != ChunkSyncService.SyncOutcome.STORED) {
                return;
            }
            file.setStatus(FileStatus.READY);
            file.setErrorMessage(null);
            file.setProcessingStage(null);
        } else {
            file.setStatus(FileStatus.FAILED);
            file.setErrorMessage(request.errorMessage());
            file.setProcessingStage(null);
        }
    }

    private String validateSuccessPayload(IngestCompleteRequest request) {
        if (request.indexVersion() == null) {
            return "성공 콜백에 index_version이 없습니다.";
        }
        List<IngestCompleteRequest.ChunkPayload> chunks = request.chunks();
        if (chunks == null || chunks.isEmpty()) {
            return "성공 콜백에 청크 데이터가 없습니다.";
        }
        if (request.chunkCount() != null && request.chunkCount() != chunks.size()) {
            return "chunk_count와 실제 청크 수가 일치하지 않습니다.";
        }
        return null;
    }
}