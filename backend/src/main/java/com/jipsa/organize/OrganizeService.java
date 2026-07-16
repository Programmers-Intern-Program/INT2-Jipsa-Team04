package com.jipsa.organize;

import com.jipsa.common.BadRequestException;
import com.jipsa.common.exception.FileNotFoundException;
import com.jipsa.common.exception.ForbiddenException;
import com.jipsa.file.File;
import com.jipsa.file.FileRepository;
import com.jipsa.file.FileService;
import com.jipsa.folder.FolderNotFoundException;
import com.jipsa.folder.FolderRepository;
import com.jipsa.folder.FolderResponse;
import com.jipsa.folder.FolderService;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayDeque;
import java.util.Deque;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * 스마트 정리(v0) — AI 호출 자체는 아직 붙지 않은 뼈대.
 * 현재 폴더 트리 조회, AI 제안(OrganizeProposal) 검증, 검증된 제안을 실제로 반영하는
 * 로직을 담당한다. 실제 AI 호출({@link AiOrganizeClient})은 후속 작업 — 지금은 손으로
 * 만들거나 임시로 만든 제안 JSON을 그대로 검증/반영하는 것까지가 이 서비스의 책임이다.
 */
@Service
public class OrganizeService {

    private final FolderRepository folderRepository;
    private final FileRepository fileRepository;
    private final FolderService folderService;
    private final FileService fileService;

    public OrganizeService(FolderRepository folderRepository,
                            FileRepository fileRepository,
                            FolderService folderService,
                            FileService fileService) {
        this.folderRepository = folderRepository;
        this.fileRepository = fileRepository;
        this.folderService = folderService;
        this.fileService = fileService;
    }

    /** 미리보기 화면의 "현재" 쪽 — 본인 폴더 전체를 평면 목록에서 트리로 조립. */
    @Transactional(readOnly = true)
    public List<FolderTreeNode> getCurrentFolderTree(Long userId) {
        List<FolderResponse> flat = folderService.list(userId);
        return buildTree(flat);
    }

    /**
     * 승인 반영 — 검증 후 (1) 제안에만 있는 새 폴더를 부모→자식 순서로 생성하고
     * (2) 각 파일을 매핑된 폴더로 이동, newName이 있으면 이름도 변경한다.
     * 전체를 하나의 트랜잭션으로 묶어서, 중간에 실패하면 새 폴더 생성분까지 포함해
     * 전부 롤백된다(부분 반영 방지).
     */
    @Transactional
    public void applyProposal(Long userId, OrganizeProposal proposal) {
        // newFolders/mappings는 요청 JSON에서 필드 자체가 생략되면 null로 역직렬화되므로,
        // 검증과 반영 양쪽에서 같은 정규화된 리스트를 쓰도록 여기서 한 번만 null을 걷어낸다.
        List<ProposedFolder> newFolders = proposal.newFolders() == null ? List.of() : proposal.newFolders();
        List<FileMapping> mappings = proposal.mappings() == null ? List.of() : proposal.mappings();

        validate(userId, newFolders, mappings);

        Map<String, Long> tempIdToRealFolderId = createProposedFolders(userId, newFolders);

        for (FileMapping mapping : mappings) {
            Long resolvedFolderId = resolveTargetFolderId(mapping, tempIdToRealFolderId);
            fileService.moveToFolder(userId, mapping.fileId(), resolvedFolderId);
            if (mapping.newName() != null && !mapping.newName().isBlank()) {
                fileService.rename(userId, mapping.fileId(), mapping.newName());
            }
        }
    }

    // ---- 검증 ----

    private void validate(Long userId, List<ProposedFolder> newFolders, List<FileMapping> mappings) {
        validateProposedFolders(userId, newFolders);
        validateMappings(userId, mappings, newFolders);
    }

    private void validateProposedFolders(Long userId, List<ProposedFolder> newFolders) {
        Set<String> tempIds = new HashSet<>();
        for (ProposedFolder folder : newFolders) {
            if (folder.tempId() == null || folder.tempId().isBlank()) {
                throw new BadRequestException("새 폴더 제안에는 tempId가 필요합니다.");
            }
            if (!tempIds.add(folder.tempId())) {
                throw new BadRequestException("새 폴더 제안의 tempId가 중복되었습니다: " + folder.tempId());
            }
            if (folder.name() == null || folder.name().isBlank()) {
                throw new BadRequestException("새 폴더 이름은 비어 있을 수 없습니다: " + folder.tempId());
            }
            if (folder.parentTempId() != null && folder.parentFolderId() != null) {
                throw new BadRequestException(
                        "새 폴더의 부모는 parentTempId/parentFolderId 중 하나만 지정할 수 있습니다: " + folder.tempId());
            }
            if (folder.parentFolderId() != null) {
                folderRepository.findByIdAndUsersId(folder.parentFolderId(), userId)
                        .orElseThrow(() -> new FolderNotFoundException(folder.parentFolderId()));
            }
        }

        for (ProposedFolder folder : newFolders) {
            if (folder.parentTempId() != null && !tempIds.contains(folder.parentTempId())) {
                throw new BadRequestException("존재하지 않는 parentTempId를 참조했습니다: " + folder.parentTempId());
            }
        }

        detectProposedFolderCycle(newFolders);
    }

    /** 새 폴더끼리의 parentTempId 참조에 순환이 있는지 확인한다(자기참조 포함). */
    private void detectProposedFolderCycle(List<ProposedFolder> newFolders) {
        // Collectors.toMap은 값이 null이면(=parentTempId 없는 루트 새 폴더) NPE를 던지기 때문에
        // 직접 HashMap을 채운다.
        Map<String, String> parentTempIdByTempId = new HashMap<>();
        for (ProposedFolder folder : newFolders) {
            parentTempIdByTempId.put(folder.tempId(), folder.parentTempId());
        }

        for (String tempId : parentTempIdByTempId.keySet()) {
            Set<String> visited = new HashSet<>();
            String current = tempId;
            while (current != null) {
                if (!visited.add(current)) {
                    throw new BadRequestException("새 폴더 제안에 순환 참조가 있습니다: " + tempId);
                }
                current = parentTempIdByTempId.get(current);
            }
        }
    }

    private void validateMappings(Long userId, List<FileMapping> mappings, List<ProposedFolder> newFolders) {
        Set<String> tempIds = newFolders.stream().map(ProposedFolder::tempId).collect(Collectors.toSet());
        Set<Long> seenFileIds = new HashSet<>();

        for (FileMapping mapping : mappings) {
            if (mapping.fileId() == null) {
                throw new BadRequestException("매핑에는 fileId가 필요합니다.");
            }
            if (!seenFileIds.add(mapping.fileId())) {
                throw new BadRequestException("같은 파일에 대한 매핑이 중복되었습니다: " + mapping.fileId());
            }

            // 파일 소유권부터 먼저 확인 — 대상 폴더 검사보다 우선한다(권한 없는 파일 조작을 조기에 차단).
            File file = fileRepository.findByIdAndDeletedAtIsNull(mapping.fileId())
                    .orElseThrow(() -> new FileNotFoundException("파일을 찾을 수 없습니다: " + mapping.fileId()));
            if (!file.getUsersId().equals(userId)) {
                throw new ForbiddenException("해당 파일에 접근할 권한이 없습니다: " + mapping.fileId());
            }

            if (mapping.targetFolderId() != null && mapping.targetTempId() != null) {
                throw new BadRequestException(
                        "targetFolderId/targetTempId 중 하나만 지정할 수 있습니다: fileId=" + mapping.fileId());
            }
            if (mapping.targetTempId() != null && !tempIds.contains(mapping.targetTempId())) {
                throw new BadRequestException("존재하지 않는 targetTempId를 참조했습니다: " + mapping.targetTempId());
            }
            if (mapping.targetFolderId() != null) {
                folderRepository.findByIdAndUsersId(mapping.targetFolderId(), userId)
                        .orElseThrow(() -> new FolderNotFoundException(mapping.targetFolderId()));
            }
        }
    }

    // ---- 반영 ----

    /** parentTempId가 없거나 이미 만들어진 폴더부터 순서대로 생성 — validate에서 순환은 이미 걷어냄. */
    private Map<String, Long> createProposedFolders(Long userId, List<ProposedFolder> newFolders) {
        Map<String, Long> tempIdToRealFolderId = new HashMap<>();
        Deque<ProposedFolder> pending = new ArrayDeque<>(newFolders);

        while (!pending.isEmpty()) {
            int sizeBefore = pending.size();
            Deque<ProposedFolder> stillPending = new ArrayDeque<>();

            for (ProposedFolder folder : pending) {
                Long parentFolderId;
                if (folder.parentTempId() != null) {
                    if (!tempIdToRealFolderId.containsKey(folder.parentTempId())) {
                        stillPending.add(folder);
                        continue;
                    }
                    parentFolderId = tempIdToRealFolderId.get(folder.parentTempId());
                } else {
                    parentFolderId = folder.parentFolderId();
                }

                Long createdId = folderService.create(userId, folder.name(), parentFolderId);
                tempIdToRealFolderId.put(folder.tempId(), createdId);
            }

            pending = stillPending;
            if (pending.size() == sizeBefore) {
                // validate 단계에서 이미 걸렀어야 하는 상황 — 방어 코드.
                throw new BadRequestException("새 폴더 제안을 생성 순서대로 정리할 수 없습니다.");
            }
        }

        return tempIdToRealFolderId;
    }

    private Long resolveTargetFolderId(FileMapping mapping, Map<String, Long> tempIdToRealFolderId) {
        if (mapping.targetTempId() != null) {
            return tempIdToRealFolderId.get(mapping.targetTempId());
        }
        return mapping.targetFolderId();
    }

    // ---- 트리 조립 ----

    private List<FolderTreeNode> buildTree(List<FolderResponse> flatFolders) {
        Map<Long, List<FolderResponse>> childrenByParentId = flatFolders.stream()
                .filter(f -> f.parentFolderId() != null)
                .collect(Collectors.groupingBy(FolderResponse::parentFolderId));

        return flatFolders.stream()
                .filter(f -> f.parentFolderId() == null)
                .map(root -> buildNode(root, childrenByParentId))
                .toList();
    }

    private FolderTreeNode buildNode(FolderResponse folder, Map<Long, List<FolderResponse>> childrenByParentId) {
        List<FolderTreeNode> children = childrenByParentId.getOrDefault(folder.folderId(), List.of()).stream()
                .map(child -> buildNode(child, childrenByParentId))
                .toList();
        return new FolderTreeNode(folder.folderId(), folder.name(), children);
    }
}
