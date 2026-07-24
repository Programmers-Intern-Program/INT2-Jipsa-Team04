package com.jipsa.chunk;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.Optional;

public interface ChunkRepository extends JpaRepository<Chunk, Long> {

    @Modifying(clearAutomatically = true)
    @Query("delete from Chunk c where c.fileId = :fileId")
    void deleteByFileId(@Param("fileId") Long fileId);

    @Query("select max(c.indexVersion) from Chunk c where c.fileId = :fileId")
    Integer findMaxIndexVersionByFileId(@Param("fileId") Long fileId);

    long countByFileId(Long fileId);

    Optional<Chunk> findByChunkIdAndFileId(String chunkId, Long fileId);
}