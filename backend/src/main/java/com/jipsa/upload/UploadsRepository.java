package com.jipsa.upload;

import org.springframework.data.jpa.repository.JpaRepository;

public interface UploadsRepository extends JpaRepository<Uploads, Long> {
}