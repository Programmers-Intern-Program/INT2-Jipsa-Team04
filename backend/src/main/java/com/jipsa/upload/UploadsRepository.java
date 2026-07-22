package com.jipsa.upload;

import org.springframework.data.jpa.repository.JpaRepository;

public interface UploadsRepository extends JpaRepository<Uploads, Long> {
    java.util.Optional<Uploads> findByUsersIdAndIdempotencyKey(Long usersId, String idempotencyKey);

    java.util.List<Uploads> findByStatusAndCreatedAtBefore(UploadStatus status, java.time.LocalDateTime createdAt);
}