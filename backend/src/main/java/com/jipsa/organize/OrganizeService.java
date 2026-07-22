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
import com.jipsa.user.UserSettingService;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.stream.Collectors;

/**
 * 스마트 정리(v0) — 현재 폴더 트리 조회, AI 제안({@link AiOrganizeClient}) 생성,
 * 제안(OrganizeProposal) 검증, 검증된 제안을 실제로 반영하는 로직을 담당한다.
 * generateProposal은 AI 응답도 반영 전 동일한 검증(validate)을 거치므로, AI가 만든
 * 제안이든 수동으로 구성한 제안이든 applyProposal에 넘기기 전 항상 같은 방어선을 통과한다.
 */
@Service
public class OrganizeService {

    private final FolderRepository folderRepository;
    private final FileRepository fileRepository;
    private final FolderService folderService;
    private final FileService fileService;
    private final OrganizeInputAssembler organizeInputAssembler;
    private final AiOrganizeClient aiOrganizeClient;
    private final UserSettingService userSettingService;

    /**
     * applyProposal 중복 반영 방지용 임시 캐시(userId:idempotencyKey → 마지막 반영 시각).
     * 정식 처리 이력 테이블(예: Reorg_Snapshot)이 아직 없어서 우선 인메모리로
     * 막아둔 것 — 서버가 여러 인스턴스로 뜨거나 재시작되면 이 방어는 사라진다(멘토링 때 확인 필요).
     */
    private static final Duration IDEMPOTENCY_TTL = Duration.ofMinutes(5);
    private final Map<String, Instant> recentlyAppliedKeys = new ConcurrentHashMap<>();

    public OrganizeService(FolderRepository folderRepository,
                            FileRepository fileRepository,
                            FolderService folderService,
                            FileService fileService,
                            OrganizeInputAssembler organizeInputAssembler,
                            AiOrganizeClient aiOrganizeClient,
                            UserSettingService userSettingService) {
        this.folderRepository = folderRepository;
        this.fileRepository = fileRepository;
        this.folderService = folderService;
        this.fileService = fileService;
        this.organizeInputAssembler = organizeInputAssembler;
        this.aiOrganizeClient = aiOrganizeClient;
        this.userSettingService = userSettingService;
    }

    /** 미리보기 화면의 "현재" 쪽 — 본인 폴더 전체를 평면 목록에서 트리로 조립. */
    @Transactional(readOnly = true)
    public List<FolderTreeNode> getCurrentFolderTree(Long userId) {
        List<FolderResponse> flat = folderService.list(userId);
        return buildTree(flat);
    }

    /**
     * AI에게 현재 폴더 트리 + 파일 목록을 넘겨 제안을 생성하고, 반영 전에 미리 검증까지
     * 마친 상태로 반환한다(존재하지 않는 id, 순환 참조 등은 여기서 걸러진다).
     *
     * 의도적으로 @Transactional을 걸지 않았다 — Anthropic 호출은 초 단위로 걸릴 수 있는데,
     * 여기에 트랜잭션을 걸면 그 시간 내내 커넥션 풀에서 커넥션 하나를 붙잡고 있게 된다.
     * 트리 조회/검증에 쓰이는 개별 리포지토리 호출은 Spring Data JPA가 각자 알아서
     * 짧은 트랜잭션으로 처리하므로 여기서 하나로 묶을 필요가 없다.
     */
    public OrganizeProposal generateProposal(Long userId) {
        List<FolderTreeNode> currentTree = getCurrentFolderTree(userId);
        List<OrganizeFileInput> files = organizeInputAssembler.assemble(userId);

        OrganizeProposal proposal = aiOrganizeClient.proposeOrganization(currentTree, files);
        List<ProposedFolder> newFolders = proposal.newFolders() == null ? List.of() : proposal.newFolders();
        List<FileMapping> mappings = proposal.mappings() == null ? List.of() : proposal.mappings();

        validate(userId, newFolders, mappings);

        return new OrganizeProposal(newFolders, mappings);
    }

    /**
     * 승인 반영 — 검증 후 (1) confidence가 사용자의 자동 분류 민감도 이상인 매핑만
     * 골라 그 매핑이 실제로 참조하는 새 폴더만 부모→자식 순서로 생성하고
     * (2) 그 매핑에 해당하는 파일만 이동, newName이 있으면 이름도 변경한다.
     * 민감도 미달(또는 confidence 자체가 이 값보다 낮은) 매핑은 아무것도 하지 않고
     * 보류 목록으로 응답에 실어 돌려준다 — 파일은 원래 위치에 그대로 남는다.
     * 전체를 하나의 트랜잭션으로 묶어서, 중간에 실패하면 새 폴더 생성분까지 포함해
     * 전부 롤백된다(부분 반영 방지).
     */
    @Transactional
    public OrganizeApplyResponse applyProposal(Long userId, OrganizeProposal proposal) {
        String idempotencyCacheKey = idempotencyCacheKey(userId, proposal.idempotencyKey());
        if (idempotencyCacheKey != null && wasRecentlyApplied(idempotencyCacheKey)) {
            // 같은 승인 동작이 짧은 시간 안에 재요청됨(예: 응답 유실 후 프론트 재시도) — 새 폴더를
            // 또 만들지 않고 이미 반영된 것으로 간주하고 조용히 성공 처리한다.
            return OrganizeApplyResponse.allApplied();
        }

        // newFolders/mappings는 요청 JSON에서 필드 자체가 생략되면 null로 역직렬화되므로,
        // 검증과 반영 양쪽에서 같은 정규화된 리스트를 쓰도록 여기서 한 번만 null을 걷어낸다.
        List<ProposedFolder> newFolders = proposal.newFolders() == null ? List.of() : proposal.newFolders();
        List<FileMapping> mappings = proposal.mappings() == null ? List.of() : proposal.mappings();

        validate(userId, newFolders, mappings);

        BigDecimal sensitivity = resolveSensitivity(userId, mappings);
        List<FileMapping> appliedMappings = new ArrayList<>();
        List<FileMapping> heldMappings = new ArrayList<>();
        for (FileMapping mapping : mappings) {
            if (isBelowThreshold(mapping.confidence(), sensitivity)) {
                heldMappings.add(mapping);
            } else {
                appliedMappings.add(mapping);
            }
        }

        // 보류된 매핑만 참조하던 새 폴더까지 만들면 아무도 안 쓰는 빈 폴더가 남으므로,
        // 실제로 반영되는 매핑이 (직접 또는 자식 폴더를 통해 간접적으로) 참조하는 새 폴더만 만든다.
        Set<String> tempIdsToCreate = resolveTempIdsToCreate(appliedMappings, newFolders);
        List<ProposedFolder> foldersToCreate = newFolders.stream()
                .filter(folder -> tempIdsToCreate.contains(folder.tempId()))
                .toList();
        Map<String, Long> tempIdToRealFolderId = createProposedFolders(userId, foldersToCreate);

        for (FileMapping mapping : appliedMappings) {
            Long resolvedFolderId = resolveTargetFolderId(mapping, tempIdToRealFolderId);
            fileService.moveToFolder(userId, mapping.fileId(), resolvedFolderId);
            if (mapping.newName() != null && !mapping.newName().isBlank()) {
                fileService.rename(userId, mapping.fileId(), mapping.newName());
            }
        }

        if (idempotencyCacheKey != null) {
            recentlyAppliedKeys.put(idempotencyCacheKey, Instant.now());
            evictExpiredIdempotencyKeys();
        }

        return new OrganizeApplyResponse(true, heldMappings);
    }

    /**
     * 매핑 중 confidence가 있는 게 하나도 없으면(기존 호출부와의 호환 케이스) 민감도 조회 자체를
     * 건너뛴다 — 어차피 전부 그대로 적용되므로 불필요한 UserSetting 조회를 피한다.
     */
    private BigDecimal resolveSensitivity(Long userId, List<FileMapping> mappings) {
        boolean anyConfidencePresent = mappings.stream().anyMatch(mapping -> mapping.confidence() != null);
        if (!anyConfidencePresent) {
            return null;
        }
        return userSettingService.getOrCreate(userId).getSensitivity();
    }

    /**
     * sensitivity가 null이면(=요청 전체에 confidence가 하나도 없는 완전 레거시 케이스) 비교 기준
     * 자체가 없으므로 필터링하지 않고 그대로 적용한다. 반면 sensitivity가 있는데(=이 배치의 다른
     * 매핑은 confidence를 채워 왔는데) 이 매핑만 confidence가 비어 있으면, AI 응답이 스키마를
     * 어겼다고 보고 안전하게 보류한다 — 여기서 그대로 적용해버리면 confidence 기반 안전장치가
     * 매핑 하나만 값을 안 줘도 무력화된다.
     */
    private boolean isBelowThreshold(Double confidence, BigDecimal sensitivity) {
        if (sensitivity == null) {
            return false;
        }
        if (confidence == null) {
            return true;
        }
        return BigDecimal.valueOf(confidence).compareTo(sensitivity) < 0;
    }

    /** 실제 반영되는 매핑이 참조하는 새 폴더 + 그 조상(parentTempId 체인)까지 tempId를 모은다. */
    private Set<String> resolveTempIdsToCreate(List<FileMapping> appliedMappings, List<ProposedFolder> newFolders) {
        Map<String, String> parentTempIdByTempId = new HashMap<>();
        for (ProposedFolder folder : newFolders) {
            parentTempIdByTempId.put(folder.tempId(), folder.parentTempId());
        }

        Set<String> tempIdsToCreate = new HashSet<>();
        for (FileMapping mapping : appliedMappings) {
            String tempId = mapping.targetTempId();
            while (tempId != null && tempIdsToCreate.add(tempId)) {
                tempId = parentTempIdByTempId.get(tempId);
            }
        }
        return tempIdsToCreate;
    }

    private String idempotencyCacheKey(Long userId, String idempotencyKey) {
        if (idempotencyKey == null || idempotencyKey.isBlank()) {
            return null;
        }
        return userId + ":" + idempotencyKey;
    }

    private boolean wasRecentlyApplied(String cacheKey) {
        Instant appliedAt = recentlyAppliedKeys.get(cacheKey);
        return appliedAt != null && appliedAt.isAfter(Instant.now().minus(IDEMPOTENCY_TTL));
    }

    private void evictExpiredIdempotencyKeys() {
        Instant cutoff = Instant.now().minus(IDEMPOTENCY_TTL);
        recentlyAppliedKeys.entrySet().removeIf(entry -> entry.getValue().isBefore(cutoff));
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
            if (mapping.confidence() != null && (mapping.confidence() < 0.0 || mapping.confidence() > 1.0)) {
                throw new BadRequestException(
                        "confidence는 0~1 사이여야 합니다: fileId=" + mapping.fileId());
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
