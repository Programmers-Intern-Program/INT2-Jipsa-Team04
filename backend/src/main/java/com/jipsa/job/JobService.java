package com.jipsa.job;

import org.springframework.stereotype.Service;

@Service
public class JobService {

    private final JobRepository jobRepository;

    public JobService(JobRepository jobRepository) {
        this.jobRepository = jobRepository;
    }

    public Job enqueueIngest(Long fileId, Long uploadsId) {
        Job job = new Job();
        job.setFileId(fileId);
        job.setUploadsId(uploadsId);
        job.setJobType(JobType.INGEST);
        job.setJobStatus(JobStatus.PENDING);
        return jobRepository.save(job);
    }
}