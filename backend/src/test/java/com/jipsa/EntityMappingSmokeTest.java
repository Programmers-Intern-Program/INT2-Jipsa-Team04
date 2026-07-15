package com.jipsa;

import com.jipsa.admin.SanctionStatus;
import com.jipsa.admin.SanctionType;
import com.jipsa.admin.UserSanction;
import com.jipsa.admin.UserSanctionRepository;
import com.jipsa.file.File;
import com.jipsa.file.FileRepository;
import com.jipsa.file.FileStatus;
import com.jipsa.job.Job;
import com.jipsa.job.JobRepository;
import com.jipsa.job.JobStatus;
import com.jipsa.job.JobType;
import com.jipsa.upload.UploadStatus;
import com.jipsa.upload.Uploads;
import com.jipsa.upload.UploadsRepository;
import com.jipsa.user.Users;
import com.jipsa.user.UsersRepository;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.data.jpa.test.autoconfigure.DataJpaTest;

import static org.assertj.core.api.Assertions.assertThat;

@DataJpaTest
class EntityMappingSmokeTest {

    @Autowired
    private FileRepository fileRepository;

    @Autowired
    private UploadsRepository uploadsRepository;

    @Autowired
    private JobRepository jobRepository;

    @Autowired
    private UsersRepository usersRepository;

    @Autowired
    private UserSanctionRepository userSanctionRepository;

    @Test
    void uploadsPersistsWithDefaults() {
        Uploads uploads = new Uploads();
        uploads.setUsersId(1L);
        uploads.setTotal(2);

        Uploads saved = uploadsRepository.save(uploads);

        assertThat(saved.getId()).isNotNull();
        assertThat(saved.getStatus()).isEqualTo(UploadStatus.PENDING);
        assertThat(saved.getCreatedAt()).isNotNull();
    }

    @Test
    void filePersistsWithDefaults() {
        File file = new File();
        file.setUsersId(1L);
        file.setName("test.pdf");
        file.setS3Key("files/abc-123");
        file.setFileType("pdf");
        file.setSizeBytes(1024L);

        File saved = fileRepository.save(file);

        assertThat(saved.getId()).isNotNull();
        assertThat(saved.getStatus()).isEqualTo(FileStatus.UPLOADED);
        assertThat(saved.isPiiDetected()).isFalse();
        assertThat(saved.getCreatedAt()).isNotNull();
        assertThat(saved.getUpdatedAt()).isNotNull();
    }

    @Test
    void jobPersistsWithDefaults() {
        Job job = new Job();
        job.setFileId(1L);
        job.setUploadsId(1L);
        job.setJobType(JobType.INGEST);

        Job saved = jobRepository.save(job);

        assertThat(saved.getId()).isNotNull();
        assertThat(saved.getJobStatus()).isEqualTo(JobStatus.PENDING);
        assertThat(saved.getMaxAttempts()).isEqualTo(3);
        assertThat(saved.getCreatedAt()).isNotNull();
    }

    @Test
    void findTopByFileIdReturnsLatestJob() {
        Job job = new Job();
        job.setFileId(99L);
        job.setJobType(JobType.INGEST);
        jobRepository.save(job);

        assertThat(jobRepository.findTopByFileIdOrderByCreatedAtDesc(99L))
                .isPresent()
                .get()
                .extracting(Job::getAttempts)
                .isEqualTo(0);
    }

    @Test
    void userSanctionPersistsWithDefaults() {
        Users target = usersRepository.save(new Users());
        Users admin = usersRepository.save(new Users());

        UserSanction sanction = new UserSanction(
                target.getId(), admin.getId(), SanctionType.TEMP_SUSPEND,
                "약관 위반 신고 누적", "ACTIVE", null);

        UserSanction saved = userSanctionRepository.save(sanction);

        assertThat(saved.getId()).isNotNull();
        assertThat(saved.getSanctionStatus()).isEqualTo(SanctionStatus.ACTIVE);
        assertThat(saved.getStartedAt()).isNotNull();
        assertThat(saved.getLiftedAt()).isNull();
    }
}