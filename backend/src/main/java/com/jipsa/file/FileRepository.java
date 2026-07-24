package com.jipsa.file;

import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

public interface FileRepository extends JpaRepository<File, Long> {

    Optional<File> findByIdAndDeletedAtIsNull(Long id);

    @Query("select f.id from File f where f.uploadsId = :uploadsId order by f.id")
    List<Long> findIdsByUploadsId(@Param("uploadsId") Long uploadsId);

    List<File> findByUploadsId(Long uploadsId);

    List<File> findByUsersIdAndDeletedAtIsNullOrderByCreatedAtDesc(Long usersId, Pageable pageable);

    Page<File> findByUsersIdAndDeletedAtIsNotNullOrderByDeletedAtDesc(Long userId, Pageable pageable);

    /** 폴더 소프트 삭제 시 함께 휴지통으로 보낼 활성 파일 조회용. */
    List<File> findByFolderIdInAndDeletedAtIsNull(List<Long> folderIds);

    /** 폴더 영구삭제 시 정리 대상 — 이 폴더들 아래 있는 삭제된 파일 전부(휴지통에 있던 시점 무관). */
    List<File> findByFolderIdInAndDeletedAtIsNotNull(List<Long> folderIds);

    /**
     * 폴더 복원 시 함께 복원할 파일 조회용 — 이 폴더가 삭제될 때 "같이" 삭제된 파일만 고른다
     * (deletedAt이 폴더 자신의 삭제 시각과 정확히 같은 것만). 폴더 삭제보다 먼저, 별개로
     * 휴지통에 들어간 파일까지 폴더 복원에 딸려서 되살아나는 걸 막기 위함.
     */
    List<File> findByFolderIdInAndDeletedAt(List<Long> folderIds, LocalDateTime deletedAt);

    @Query("select coalesce(sum(f.sizeBytes), 0) from File f " +
            "where f.usersId = :userId and f.deletedAt is null")
    long sumSizeBytesByUsersId(@Param("userId") Long userId);

    /** 스마트 정리 AI 입력 조립(OrganizeInputAssembler)용 — 본인 소유의 삭제되지 않은 파일 전체. */
    List<File> findByUsersIdAndDeletedAtIsNull(Long usersId);

    @Query("select f from File f where f.usersId = :userId and f.deletedAt is null " +
            "and (:folderId is null or f.folderId = :folderId) " +
            "and (:keyword is null or f.name like concat('%', :keyword, '%') escape '\\') " +
            "and (:docType is null or f.fileType = :docType) " +
            "and (:dateFrom is null or f.createdAt >= :dateFrom) " +
            "and (:dateTo is null or f.createdAt <= :dateTo) " +
            "and (:documentType is null or exists (select m2 from FileMetadata m2 " +
            "where m2.fileId = f.id and m2.documentType = :documentType)) " +
            "and (:tag is null or exists (select m from FileMetadata m " +
            "where m.fileId = f.id and m.tags like concat('%\"', :tag, '\"%')))")
    Page<File> search(@Param("userId") Long userId,
                      @Param("folderId") Long folderId,
                      @Param("keyword") String keyword,
                      @Param("docType") String docType,
                      @Param("tag") String tag,
                      @Param("dateFrom") LocalDateTime dateFrom,
                      @Param("dateTo") LocalDateTime dateTo,
                      @Param("documentType") String documentType,
                      Pageable pageable);
}