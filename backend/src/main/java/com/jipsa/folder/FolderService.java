package com.jipsa.folder;

import com.jipsa.common.BadRequestException;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import com.jipsa.file.FileService;

import java.time.LocalDateTime;
import java.time.temporal.ChronoUnit;
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

    private static final int TRASH_PAGE_SIZE = 20;

    private final FolderRepository folderRepository;
    private final FileService fileService;

    public FolderService(FolderRepository folderRepository, FileService fileService) {
        this.folderRepository = folderRepository;
        this.fileService = fileService;
    }

    /** GET /api/v1/folders — 본인 소유 활성 전체 평면 목록. */
    @Transactional(readOnly = true)
    public List<FolderResponse> list(Long userId) {
        return folderRepository.findByUsersId(userId).stream()
                .map(FolderResponse::from)
                .toList();
    }

    /** GET /api/v1/folders/trash — 휴지통 목록. */
    @Transactional(readOnly = true)
    public FolderTrashListResponse listTrash(Long userId, int page) {
        Pageable pageable = PageRequest.of(page, TRASH_PAGE_SIZE);
        Page<Folder> result = folderRepository.findByUsersIdAndDeletedAtIsNotNullOrderByDeletedAtDesc(userId, pageable);
        List<FolderResponse> items = result.getContent().stream().map(FolderResponse::from).toList();
        return new FolderTrashListResponse(items, result.getTotalElements(), result.getNumber(), result.getSize());
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

    /** DELETE /api/v1/folders/{id} — 하위 폴더 전부 소프트 삭제(휴지통 이동), 삭제 전 소유자 검증. */
    @Transactional
    public void delete(Long userId, Long folderId) {
        folderRepository.findByIdAndUsersId(folderId, userId)
                .orElseThrow(() -> new FolderNotFoundException(folderId));

        Map<Long, List<Folder>> childrenByParentId = folderRepository.findByUsersId(userId).stream()
                .filter(f -> f.getParentFolderId() != null)
                .collect(Collectors.groupingBy(Folder::getParentFolderId));

        List<Long> subtreeIds = collectSubtreeIds(folderId, childrenByParentId);

        // 나노초 단위까지 그대로 두면 DB(DATETIME(6))에 왕복 저장했다 다시 읽어올 때
        // 정밀도가 깎여서, 나중에 복원할 때 "이 값과 정확히 같은 것만" 찾는 비교가
        // 어긋날 수 있다(실제로 그렇게 실패하는 게 확인됨) — 그래서 밀리초로 미리 잘라둔다.
        LocalDateTime now = LocalDateTime.now().truncatedTo(ChronoUnit.MILLIS);
        folderRepository.findAllById(subtreeIds).forEach(f -> f.setDeletedAt(now));
        // 폴더와 정확히 같은 deletedAt을 넘겨서, 나중에 복원할 때 "이번 삭제로 같이 딸려간 파일"만
        // 골라 복원할 수 있게 한다(FileService.restoreByFolderIds 참고).
        fileService.softDeleteByFolderIds(subtreeIds, now);
    }

    /** PATCH /api/v1/folders/{id}/restore — 휴지통의 폴더를 복원, 하위 폴더·파일도 함께 복원. */
    @Transactional
    public void restore(Long userId, Long folderId) {
        Folder folder = folderRepository.findByIdAndUsersIdIncludingDeleted(folderId, userId)
                .orElseThrow(() -> new FolderNotFoundException(folderId));
        if (folder.getDeletedAt() == null) {
            throw new BadRequestException("삭제되지 않은 폴더입니다: " + folderId);
        }
        // forEach로 deletedAt을 지우기 전에 미리 값을 떠 둔다 — folder도 findAllById가 반환하는
        // 영속 엔티티와 동일 인스턴스라, 나중에 읽으면 이미 null로 바뀐 뒤일 수 있다.
        LocalDateTime deletedAt = folder.getDeletedAt();

        Map<Long, List<Folder>> childrenByParentId = folderRepository.findByUsersIdIncludingDeleted(userId).stream()
                .filter(f -> f.getParentFolderId() != null)
                .collect(Collectors.groupingBy(Folder::getParentFolderId));

        List<Long> subtreeIds = collectSubtreeIds(folderId, childrenByParentId);

        // subtreeIds에는 트리 구조상 하위에 있는 폴더가 전부 잡히지만, 그중 실제로 "이번
        // 삭제로 같이 딸려간" 폴더만 복원한다(deletedAt이 정확히 같은 것만). 하위 폴더가
        // 이 폴더보다 먼저 따로 삭제돼 있었다면(다른 deletedAt) 복원 대상에서 제외된다.
        folderRepository.findAllById(subtreeIds).stream()
                .filter(f -> deletedAt.equals(f.getDeletedAt()))
                .forEach(f -> f.setDeletedAt(null));
        // 파일도 마찬가지로 deletedAt이 정확히 같은 것만 복원 — 이 폴더가 삭제되기 전에 이미
        // 따로 휴지통에 있던 파일까지 되살아나는 걸 막는다.
        fileService.restoreByFolderIds(subtreeIds, deletedAt);

        // 복원 대상(folder, 이번 요청의 폴더 자신)의 부모가 여전히 삭제된 상태로 남아있으면
        // 그 밑에 매달아 둘 수 없다 — 나중에 그 부모가 영구삭제될 때 자기참조 FK가 깨지는 걸
        // 막기 위해 루트로 꺼내놓는다(FileService.restore()의 동일한 처리와 같은 이유).
        if (folder.getParentFolderId() != null && isFolderMissingOrDeleted(folder.getParentFolderId())) {
            folder.setParentFolderId(null);
        }
    }

    private boolean isFolderMissingOrDeleted(Long folderId) {
        return folderRepository.findById(folderId)
                .map(f -> f.getDeletedAt() != null)
                .orElse(true);
    }

    /** DELETE /api/v1/folders/{id}/permanent — 휴지통의 폴더를 영구 삭제, 하위 파일 S3 실물까지 정리. */
    @Transactional
    public void permanentDelete(Long userId, Long folderId) {
        Folder folder = folderRepository.findByIdAndUsersIdIncludingDeleted(folderId, userId)
                .orElseThrow(() -> new FolderNotFoundException(folderId));
        if (folder.getDeletedAt() == null) {
            throw new BadRequestException("휴지통에 있는 폴더만 영구 삭제할 수 있습니다.");
        }

        Map<Long, List<Folder>> childrenByParentId = folderRepository.findByUsersIdIncludingDeleted(userId).stream()
                .filter(f -> f.getParentFolderId() != null)
                .collect(Collectors.groupingBy(Folder::getParentFolderId));

        List<Long> subtreeIds = collectSubtreeIds(folderId, childrenByParentId);

        fileService.permanentDeleteByFolderIds(subtreeIds);
        // 삭제 순서: DDL의 FK_Folder_ParentFolder(자기참조 FK)에 ON DELETE CASCADE가 없어서,
        // 자식이 아직 참조 중인 부모를 먼저 지우면 FK 제약 위반이 난다. collectSubtreeIds는
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
