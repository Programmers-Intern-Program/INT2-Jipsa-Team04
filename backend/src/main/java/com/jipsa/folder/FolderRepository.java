package com.jipsa.folder;

import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;
import java.util.Optional;

public interface FolderRepository extends JpaRepository<Folder, Long> {

    /** GET /api/v1/folders — 본인 소유 활성(삭제되지 않은) 전체 평면 목록. */
    @Query("select f from Folder f where f.usersId = :usersId and f.deletedAt is null")
    List<Folder> findByUsersId(@Param("usersId") Long usersId);

    /** 단건 조회 시 소유권까지 한 번에 검증(다른 사용자 폴더거나 삭제된 폴더면 empty). */
    @Query("select f from Folder f where f.id = :id and f.usersId = :usersId and f.deletedAt is null")
    Optional<Folder> findByIdAndUsersId(@Param("id") Long id, @Param("usersId") Long usersId);

    /** 삭제된 폴더까지 포함해 전부 조회 — 휴지통 복원/영구삭제 시 서브트리 탐색용. */
    @Query("select f from Folder f where f.usersId = :usersId")
    List<Folder> findByUsersIdIncludingDeleted(@Param("usersId") Long usersId);

    /** 삭제된 폴더 단건 조회(복원/영구삭제 대상 자체를 가져올 때 사용, 삭제 여부는 호출부에서 확인). */
    @Query("select f from Folder f where f.id = :id and f.usersId = :usersId")
    Optional<Folder> findByIdAndUsersIdIncludingDeleted(@Param("id") Long id, @Param("usersId") Long usersId);

    /** GET /api/v1/folders/trash — 휴지통 목록. */
    Page<Folder> findByUsersIdAndDeletedAtIsNotNullOrderByDeletedAtDesc(Long usersId, Pageable pageable);

    /** 재귀 삭제 시 자식 폴더 탐색용. */
    List<Folder> findByParentFolderId(Long parentFolderId);
}
