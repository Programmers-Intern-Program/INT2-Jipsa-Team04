package com.jipsa.file;

import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.LocalDateTime;
import java.util.List;

public interface FileMetadataRepository extends JpaRepository<FileMetadata, Long> {

    @Query("select m.fileId from FileMetadata m, File f "
            + "where f.id = m.fileId and f.status = com.jipsa.file.FileStatus.READY "
            + "and f.deletedAt is null and m.extractionStatus = 'PROCESSING' "
            + "order by m.fileId")
    List<Long> findFileIdsPendingAiMetadata(Pageable pageable);

    @Modifying(clearAutomatically = true)
    @Query("update FileMetadata m set m.extractionStatus = 'GENERATING', "
            + "m.extractionIndexVersion = (select max(c.indexVersion) from Chunk c where c.fileId = m.fileId), "
            + "m.updatedAt = :now "
            + "where m.fileId = :id and m.extractionStatus = 'PROCESSING' "
            + "and exists (select f.id from File f where f.id = m.fileId "
            + "and f.status = com.jipsa.file.FileStatus.READY and f.deletedAt is null)")
    int claimForGeneration(@Param("id") Long id, @Param("now") LocalDateTime now);

    @Modifying(clearAutomatically = true)
    @Query("update FileMetadata m set m.extractionStatus = 'PROCESSING', m.updatedAt = :now "
            + "where m.extractionStatus = 'GENERATING' and m.updatedAt < :threshold")
    int resetStaleGenerating(@Param("threshold") LocalDateTime threshold, @Param("now") LocalDateTime now);

    @Modifying(clearAutomatically = true)
    @Query("update FileMetadata m set m.extractionStatus = 'SKIPPED', m.updatedAt = :now "
            + "where m.extractionStatus = 'PROCESSING' and exists (select f.id from File f "
            + "where f.id = m.fileId and f.status = com.jipsa.file.FileStatus.READY and f.deletedAt is null)")
    int markPendingSkipped(@Param("now") LocalDateTime now);
}