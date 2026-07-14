package com.jipsa.folder;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface FolderRepository extends JpaRepository<Folder, Long> {

    /** GET /api/v1/folders — 본인 소유 전체 평면 목록. */
    List<Folder> findByUsersId(Long usersId);

    /** 단건 조회 시 소유권까지 한 번에 검증(다른 사용자 폴더면 empty). */
    Optional<Folder> findByIdAndUsersId(Long id, Long usersId);

    /** 재귀 삭제 시 자식 폴더 탐색용. */
    List<Folder> findByParentFolderId(Long parentFolderId);
}
