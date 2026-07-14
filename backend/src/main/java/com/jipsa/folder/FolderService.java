package com.jipsa.folder;

import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Deque;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

@Service
public class FolderService {

    private final FolderRepository folderRepository;

    public FolderService(FolderRepository folderRepository) {
        this.folderRepository = folderRepository;
    }

    /** GET /api/v1/folders — 본인 소유 전체 평면 목록. */
    @Transactional(readOnly = true)
    public List<FolderResponse> list(Long userId) {
        return folderRepository.findByUsersId(userId).stream()
                .map(FolderResponse::from)
                .toList();
    }

    /** POST /api/v1/folders — parentFolderId 미지정(null) 시 루트에 생성. */
    @Transactional
    public Long create(Long userId, String name, Long parentFolderId) {
        String trimmedName = requireNonBlank(name);

        if (parentFolderId != null) {
            folderRepository.findByIdAndUsersId(parentFolderId, userId)
                    .orElseThrow(() -> new FolderNotFoundException(parentFolderId));
        }

        Folder folder = new Folder(userId, trimmedName, parentFolderId);
        return folderRepository.save(folder).getId();
    }

    /**
     * PATCH /api/v1/folders/{id} — 이름 변경 및/또는 부모 폴더 이동.
     * name/parentFolderId 둘 다 선택 필드라 "필드가 요청에 있었는지"를 컨트롤러에서
     * *Provided 플래그로 넘겨받는다(단순 null 체크로는 "값을 null로 바꾸는 것"과
     * "필드를 아예 안 보낸 것"을 구분할 수 없기 때문 — 특히 루트로 이동시키는
     * parentFolderId=null 요청과, 이름만 바꾸는 요청을 구분해야 함).
     */
    @Transactional
    public void update(Long userId, Long folderId,
                        String name, boolean nameProvided,
                        Long parentFolderId, boolean parentFolderIdProvided) {
        Folder folder = folderRepository.findByIdAndUsersId(folderId, userId)
                .orElseThrow(() -> new FolderNotFoundException(folderId));

        if (nameProvided) {
            folder.setName(requireNonBlank(name));
        }

        if (parentFolderIdProvided) {
            if (parentFolderId == null) {
                folder.setParentFolderId(null);   // 루트로 이동
            } else if (parentFolderId.equals(folderId)) {
                throw new FolderCircularReferenceException(folderId, parentFolderId);
            } else {
                folderRepository.findByIdAndUsersId(parentFolderId, userId)
                        .orElseThrow(() -> new FolderNotFoundException(parentFolderId));
                if (isDescendantOrSelf(userId, parentFolderId, folderId)) {
                    throw new FolderCircularReferenceException(folderId, parentFolderId);
                }
                folder.setParentFolderId(parentFolderId);
            }
        }
        // folder는 영속 상태 엔티티 — 트랜잭션 커밋 시 더티체킹으로 자동 반영(save 불필요).
    }

    /** DELETE /api/v1/folders/{id} — 하위 폴더 전부 재귀 삭제, 삭제 전 소유자 검증. */
    @Transactional
    public void delete(Long userId, Long folderId) {
        folderRepository.findByIdAndUsersId(folderId, userId)
                .orElseThrow(() -> new FolderNotFoundException(folderId));

        Map<Long, List<Folder>> childrenByParentId = folderRepository.findByUsersId(userId).stream()
                .filter(f -> f.getParentFolderId() != null)
                .collect(Collectors.groupingBy(Folder::getParentFolderId));

        List<Long> subtreeIds = collectSubtreeIds(folderId, childrenByParentId);
        // deleteAllByIdInBatch()는 벌크 DELETE SQL 한 방으로 처리되지만 영속성 컨텍스트를
        // 갱신하지 않는다 — 같은 트랜잭션 안에서 방금 지운 걸 findById로 다시 조회하면
        // 1차 캐시에 남아있는 stale 엔티티가 그대로 반환되는 문제가 있었다(테스트에서 발견).
        // deleteAllById()는 각 엔티티를 로드해서 EntityManager.remove()로 지우기 때문에
        // 영속성 컨텍스트가 즉시 갱신되어 이후 조회에서 정상적으로 빠진다.
        //
        // 삭제 순서도 중요하다: DDL의 FK_Folder_ParentFolder(자기참조 FK)에 ON DELETE CASCADE가
        // 없어서, 자식이 아직 참조 중인 부모를 먼저 지우면 FK 제약 위반이 난다. collectSubtreeIds는
        // BFS라 항상 부모가 자식보다 앞에 오므로(깊이 비내림차순), 리스트를 뒤집으면 깊이 내림차순이
        // 되어 모든 부모-자식 쌍에서 자식이 부모보다 항상 먼저 삭제된다.
        Collections.reverse(subtreeIds);
        folderRepository.deleteAllById(subtreeIds);
    }

    /** rootId 자신 + 모든 자손 folderId를 BFS로 모은다. */
    private List<Long> collectSubtreeIds(Long rootId, Map<Long, List<Folder>> childrenByParentId) {
        List<Long> ids = new ArrayList<>();
        Deque<Long> queue = new ArrayDeque<>();
        queue.add(rootId);
        while (!queue.isEmpty()) {
            Long current = queue.poll();
            ids.add(current);
            for (Folder child : childrenByParentId.getOrDefault(current, List.of())) {
                queue.add(child.getId());
            }
        }
        return ids;
    }

    /**
     * candidateParentId가 targetFolderId 자신이거나 그 자손인지 확인한다.
     * (부모 체인을 candidateParentId부터 루트까지 타고 올라가며 targetFolderId와 일치하는지 본다.
     * 일치하면 targetFolderId를 candidateParentId 아래로 옮기는 순간 순환 참조가 생긴다는 뜻.)
     */
    private boolean isDescendantOrSelf(Long userId, Long candidateParentId, Long targetFolderId) {
        Map<Long, Long> parentIdById = new HashMap<>();
        for (Folder folder : folderRepository.findByUsersId(userId)) {
            parentIdById.put(folder.getId(), folder.getParentFolderId());
        }

        Long current = candidateParentId;
        while (current != null) {
            if (current.equals(targetFolderId)) {
                return true;
            }
            current = parentIdById.get(current);
        }
        return false;
    }

    private String requireNonBlank(String name) {
        if (name == null || name.isBlank()) {
            throw new IllegalArgumentException("name은 비어 있을 수 없습니다");
        }
        return name.trim();
    }
}
