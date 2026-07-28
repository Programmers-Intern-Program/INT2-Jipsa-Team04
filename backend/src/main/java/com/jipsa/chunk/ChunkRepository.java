package com.jipsa.chunk;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.data.domain.Pageable;

import java.util.Optional;
import java.util.List;

public interface ChunkRepository extends JpaRepository<Chunk, Long> {

    @Modifying(flushAutomatically = true, clearAutomatically = true)
    @Query("delete from Chunk c where c.fileId = :fileId")
    void deleteByFileId(@Param("fileId") Long fileId);

    @Query("select max(c.indexVersion) from Chunk c where c.fileId = :fileId")
    Integer findMaxIndexVersionByFileId(@Param("fileId") Long fileId);

    long countByFileId(Long fileId);

    Optional<Chunk> findByChunkIdAndFileId(String chunkId, Long fileId);

    List<Chunk> findByFileIdOrderByChunkIndexAsc(Long fileId, Pageable pageable);
}
