package com.jipsa.internal;

import com.jipsa.chunk.ChunkSyncService;
import com.jipsa.common.exception.FileNotFoundException;
import com.jipsa.file.File;
import com.jipsa.file.FileRepository;
import com.jipsa.file.FileStatus;
import com.jipsa.job.JobRepository;
import com.jipsa.job.JobStatus;
import com.jipsa.job.JobType;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;

@Service
public class IngestCallbackService {

    private static final Logger log = LoggerFactory.getLogger(IngestCallbackService.class);

    private final FileRepository fileRepository;
    private final ChunkSyncService chunkSyncService;
    private final JobRepository jobRepository;

    public IngestCallbackService(FileRepository fileRepository,
                                 ChunkSyncService chunkSyncService,
                                 JobRepository jobRepository) {
        this.fileRepository = fileRepository;
        this.chunkSyncService = chunkSyncService;
        this.jobRepository = jobRepository;
    }

    @Transactional
    public void complete(Long fileIdx, IngestCompleteRequest request) {
        File file = fileRepository.findForUpdate(fileIdx)
                .orElseThrow(() -> new FileNotFoundException("파일을 찾을 수 없습니다: " + fileIdx));
        if (!request.success()) {
            log.info("RAG 실패 콜백 무시 (file {}): {} - 타임아웃 복구에 위임", fileIdx, request.errorMessage());
            return;
        }
        String inconsistency = validateSuccessPayload(request);
        if (inconsistency != null) {
            log.warn("RAG 성공 콜백 페이로드 이상으로 무시 (file {}): {}", fileIdx, inconsistency);
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
        finalizeIngestJobAsSuccess(fileIdx);
    }

    private void finalizeIngestJobAsSuccess(Long fileIdx) {
        jobRepository.findTopByFileIdAndJobTypeOrderByCreatedAtDesc(fileIdx, JobType.INGEST)
                .filter(job -> job.getJobStatus() != JobStatus.CANCELLED)
                .ifPresent(job -> {
                    job.setJobStatus(JobStatus.SUCCESS);
                    job.setErrorMessage(null);
                    job.setFinishedAt(LocalDateTime.now());
                });
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