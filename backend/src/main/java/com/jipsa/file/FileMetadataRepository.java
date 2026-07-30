package com.jipsa.file;

import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.LocalDateTime;
import java.util.List;

public interface FileMetadataRepository extends JpaRepository<FileMetadata, Long> {

    @Modifying(flushAutomatically = true, clearAutomatically = true)
    @Query("delete from FileMetadata m where m.fileId = :fileId")
    void deleteByFileId(@Param("fileId") Long fileId);

    @Query("select m.fileId from FileMetadata m, File f "
            + "where f.id = m.fileId and f.status = com.jipsa.file.FileStatus.READY "
            + "and f.deletedAt is null and m.extractionStatus = 'PROCESSING' "
            + "order by m.fileId")
    List<Long> findFileIdsPendingAiMetadata(Pageable pageable);

    @Modifying(clearAutomatically = true)
    @Query("update FileMetadata m set m.extractionStatus = 'GENERATING', m.claimToken = :token, "
            + "m.extractionIndexVersion = (select max(c.indexVersion) from Chunk c where c.fileId = m.fileId), "
            + "m.updatedAt = :now "
            + "where m.fileId = :id and m.extractionStatus = 'PROCESSING' "
            + "and exists (select f.id from File f where f.id = m.fileId "
            + "and f.status = com.jipsa.file.FileStatus.READY and f.deletedAt is null)")
    int claimForGeneration(@Param("id") Long id, @Param("token") String token, @Param("now") LocalDateTime now);

    @Modifying(clearAutomatically = true)
    @Query("update FileMetadata m set m.summary = :summary, m.keywords = :keywords, "
            + "m.extractedEntities = :entities, m.extractionConfidence = :confidence, "
            + "m.documentType = case when m.documentType is null or m.documentType = '' "
            + "then :documentType else m.documentType end, "
            + "m.extractionStatus = 'READY', m.updatedAt = :now "
            + "where m.fileId = :id and m.extractionStatus = 'GENERATING' and m.claimToken = :token")
    int completeGeneration(@Param("id") Long id, @Param("token") String token,
                           @Param("summary") String summary, @Param("keywords") String keywords,
                           @Param("entities") String entities, @Param("confidence") Double confidence,
                           @Param("documentType") String documentType, @Param("now") LocalDateTime now);

    @Modifying(clearAutomatically = true)
    @Query("update FileMetadata m set m.extractionStatus = 'FAILED', m.updatedAt = :now "
            + "where m.fileId = :id and m.extractionStatus = 'GENERATING' and m.claimToken = :token")
    int failGeneration(@Param("id") Long id, @Param("token") String token, @Param("now") LocalDateTime now);

    @Modifying
    @Query("update FileMetadata m set m.summary = :summary, m.keywords = :keywords, "
            + "m.extractedEntities = :entities, m.extractionConfidence = :confidence, "
            + "m.extractionStatus = 'READY', "
            + "m.extractionIndexVersion = case when :indexVersion is null "
            + "then m.extractionIndexVersion else :indexVersion end, m.updatedAt = :now "
            + "where m.fileId = :id and (:indexVersion is null or m.extractionIndexVersion is null "
            + "or :indexVersion >= m.extractionIndexVersion)")
    int applyCallbackSuccess(@Param("id") Long id, @Param("summary") String summary,
                             @Param("keywords") String keywords, @Param("entities") String entities,
                             @Param("confidence") Double confidence, @Param("indexVersion") Integer indexVersion,
                             @Param("now") LocalDateTime now);

    @Modifying
    @Query("update FileMetadata m set m.extractionStatus = 'FAILED', "
            + "m.extractionIndexVersion = case when :indexVersion is null "
            + "then m.extractionIndexVersion else :indexVersion end, m.updatedAt = :now "
            + "where m.fileId = :id and (:indexVersion is null or m.extractionIndexVersion is null "
            + "or :indexVersion >= m.extractionIndexVersion)")
    int applyCallbackFailure(@Param("id") Long id, @Param("indexVersion") Integer indexVersion,
                             @Param("now") LocalDateTime now);

    @Modifying
    @Query("update FileMetadata m set m.tags = :tags, m.updatedAt = :now where m.fileId = :id")
    int updateTags(@Param("id") Long id, @Param("tags") String tags, @Param("now") LocalDateTime now);

    @Modifying
    @Query("update FileMetadata m set m.documentType = :documentType, m.updatedAt = :now where m.fileId = :id")
    int updateDocumentType(@Param("id") Long id, @Param("documentType") String documentType,
                           @Param("now") LocalDateTime now);

    @Modifying
    @Query("update FileMetadata m set m.extractionStatus = :status, m.updatedAt = :now where m.fileId = :id")
    int updateExtractionStatus(@Param("id") Long id, @Param("status") String status,
                               @Param("now") LocalDateTime now);

    @Modifying(clearAutomatically = true)
    @Query("update FileMetadata m set m.extractionStatus = 'PROCESSING', m.claimToken = null, m.updatedAt = :now "
            + "where m.extractionStatus = 'GENERATING' and m.updatedAt < :threshold")
    int resetStaleGenerating(@Param("threshold") LocalDateTime threshold, @Param("now") LocalDateTime now);

    @Modifying(clearAutomatically = true)
    @Query("update FileMetadata m set m.extractionStatus = 'SKIPPED', m.updatedAt = :now "
            + "where m.extractionStatus = 'PROCESSING' and exists (select f.id from File f "
            + "where f.id = m.fileId and f.status = com.jipsa.file.FileStatus.READY and f.deletedAt is null)")
    int markPendingSkipped(@Param("now") LocalDateTime now);
}
