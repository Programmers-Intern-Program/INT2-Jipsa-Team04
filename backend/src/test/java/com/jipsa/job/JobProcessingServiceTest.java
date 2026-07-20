package com.jipsa.job;

import com.jipsa.file.File;
import com.jipsa.file.FileRepository;
import com.jipsa.file.FileStatus;
import com.jipsa.internal.IngestManifestService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class JobProcessingServiceTest {

    @Mock
    private JobRepository jobRepository;
    @Mock
    private FileRepository fileRepository;
    @Mock
    private IngestManifestService ingestManifestService;
    @Mock
    private RagIngestClient ragIngestClient;

    private JobProcessingService service;

    @BeforeEach
    void setUp() {
        service = new JobProcessingService(jobRepository, fileRepository,
                ingestManifestService, ragIngestClient, 1000L);
    }

    private Job runningJob(int attempts) {
        Job job = new Job();
        job.setId(1L);
        job.setFileId(10L);
        job.setJobType(JobType.INGEST);
        job.setJobStatus(JobStatus.RUNNING);
        job.setAttempts(attempts);
        job.setMaxAttempts(3);
        return job;
    }

    private File uploadedFile() {
        File file = new File();
        file.setId(10L);
        file.setUsersId(1L);
        file.setStatus(FileStatus.UPLOADED);
        return file;
    }

    @Test
    void successHandsOffToRagAndLeavesFileProcessing() {
        Job job = runningJob(1);
        File file = uploadedFile();
        when(jobRepository.findById(1L)).thenReturn(Optional.of(job));
        when(fileRepository.findById(10L)).thenReturn(Optional.of(file));

        service.process(1L);

        assertThat(file.getStatus()).isEqualTo(FileStatus.PROCESSING);
        assertThat(file.getProcessingStage()).isNull();
        assertThat(job.getJobStatus()).isEqualTo(JobStatus.SUCCESS);
        assertThat(job.getFinishedAt()).isNotNull();
    }

    @Test
    void failureBelowMaxAttemptsSchedulesRetry() {
        Job job = runningJob(1);
        File file = uploadedFile();
        when(jobRepository.findById(1L)).thenReturn(Optional.of(job));
        when(fileRepository.findById(10L)).thenReturn(Optional.of(file));
        doThrow(new RuntimeException("boom")).when(ragIngestClient).push(any());

        service.process(1L);

        assertThat(job.getJobStatus()).isEqualTo(JobStatus.RETRY_WAIT);
        assertThat(job.getNextAttemptAt()).isNotNull();
        assertThat(job.getWorkerId()).isNull();
        assertThat(job.getErrorMessage()).isEqualTo("boom");
        assertThat(file.getStatus()).isNotEqualTo(FileStatus.FAILED);
    }

    @Test
    void failureAtMaxAttemptsMarksFailed() {
        Job job = runningJob(3);
        File file = uploadedFile();
        when(jobRepository.findById(1L)).thenReturn(Optional.of(job));
        when(fileRepository.findById(10L)).thenReturn(Optional.of(file));
        doThrow(new RuntimeException("boom")).when(ragIngestClient).push(any());

        service.process(1L);

        assertThat(job.getJobStatus()).isEqualTo(JobStatus.FAILED);
        assertThat(file.getStatus()).isEqualTo(FileStatus.FAILED);
        assertThat(file.getErrorMessage()).isEqualTo("boom");
    }

    @Test
    void ignoresJobThatIsNotRunning() {
        Job job = runningJob(1);
        job.setJobStatus(JobStatus.PENDING);
        when(jobRepository.findById(1L)).thenReturn(Optional.of(job));
        lenient().when(fileRepository.findById(10L)).thenReturn(Optional.of(uploadedFile()));

        service.process(1L);

        assertThat(job.getJobStatus()).isEqualTo(JobStatus.PENDING);
    }
}