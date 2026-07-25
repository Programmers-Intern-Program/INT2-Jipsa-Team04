package com.jipsa.file;

import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;

import java.util.List;

public interface FileMetadataRepository extends JpaRepository<FileMetadata, Long> {

    @Query("select m.fileId from FileMetadata m, File f "
            + "where f.id = m.fileId and f.status = com.jipsa.file.FileStatus.READY "
            + "and m.extractionStatus = 'PROCESSING'")
    List<Long> findFileIdsPendingAiMetadata(Pageable pageable);
}