package com.jipsa.file;

import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.Optional;

public interface FileRepository extends JpaRepository<File, Long> {

    Optional<File> findByIdAndDeletedAtIsNull(Long id);

    @Query("select f from File f where f.usersId = :userId and f.deletedAt is null " +
            "and (:folderId is null or f.folderId = :folderId) " +
            "and (:keyword is null or f.name like concat('%', :keyword, '%') escape '\\') " +
            "and (:docType is null or f.fileType = :docType)")
    Page<File> search(@Param("userId") Long userId,
                      @Param("folderId") Long folderId,
                      @Param("keyword") String keyword,
                      @Param("docType") String docType,
                      Pageable pageable);
}