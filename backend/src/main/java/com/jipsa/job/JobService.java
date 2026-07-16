package com.jipsa.job;

import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;

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

    @Transactional
    public List<Long> claimBatch(String workerId, int batchSize, Duration ownershipTtl) {
        LocalDateTime now = LocalDateTime.now();
        LocalDateTime expiry = now.plus(ownershipTtl);
        List<Long> candidates = jobRepository.findClaimableIds(now, PageRequest.of(0, batchSize));
        List<Long> claimed = new ArrayList<>();
        for (Long id : candidates) {
            if (jobRepository.claim(id, workerId, now, expiry) == 1) {
                claimed.add(id);
            }
        }
        return claimed;
    }
}