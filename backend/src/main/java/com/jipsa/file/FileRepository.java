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

    Page<File> findByUsersIdAndDeletedAtIsNotNullOrderByDeletedAtDesc(Long userId, Pageable pageable);

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
            "and (:tag is null or exists (select m from FileMetadata m " +
            "where m.fileId = f.id and m.tags like concat('%\"', :tag, '\"%')))")
    Page<File> search(@Param("userId") Long userId,
                      @Param("folderId") Long folderId,
                      @Param("keyword") String keyword,
                      @Param("docType") String docType,
                      @Param("tag") String tag,
                      @Param("dateFrom") LocalDateTime dateFrom,
                      @Param("dateTo") LocalDateTime dateTo,
                      Pageable pageable);
}