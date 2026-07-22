package com.jipsa.organize;

import com.jipsa.common.BadRequestException;
import com.jipsa.common.exception.FileNotFoundException;
import com.jipsa.common.exception.ForbiddenException;
import com.jipsa.file.File;
import com.jipsa.file.FileRepository;
import com.jipsa.file.FileService;
import com.jipsa.folder.Folder;
import com.jipsa.folder.FolderNotFoundException;
import com.jipsa.folder.FolderRepository;
import com.jipsa.folder.FolderResponse;
import com.jipsa.folder.FolderService;
import com.jipsa.user.UserSetting;
import com.jipsa.user.UserSettingService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.math.BigDecimal;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class OrganizeServiceTest {

    @Mock
    private FolderRepository folderRepository;
    @Mock
    private FileRepository fileRepository;
    @Mock
    private FolderService folderService;
    @Mock
    private FileService fileService;
    @Mock
    private OrganizeInputAssembler organizeInputAssembler;
    @Mock
    private AiOrganizeClient aiOrganizeClient;
    @Mock
    private UserSettingService userSettingService;

    private OrganizeService organizeService;

    private static final Long USER = 1L;
    private static final Long OTHER_USER = 2L;

    @BeforeEach
    void setUp() {
        organizeService = new OrganizeService(folderRepository, fileRepository, folderService, fileService,
                organizeInputAssembler, aiOrganizeClient, userSettingService);
    }

    private File ownedFile(Long id) {
        File file = new File();
        file.setId(id);
        file.setUsersId(USER);
        return file;
    }

    /** confidence를 쓰는 테스트에서만 stubbing — 확신도 없는 매핑만 있는 테스트는 이 값 자체를 조회하지 않는다. */
    private void givenSensitivity(String sensitivity) {
        UserSetting setting = new UserSetting(USER);
        setting.setSensitivity(new BigDecimal(sensitivity));
        when(userSettingService.getOrCreate(USER)).thenReturn(setting);
    }

    // ---- 현재 폴더 트리 조립 ----

    @Test
    void getCurrentFolderTree_평면목록을_트리로_조립() {
        when(folderService.list(USER)).thenReturn(List.of(
                new FolderResponse(1L, "루트", null),
                new FolderResponse(2L, "자식", 1L),
                new FolderResponse(3L, "손자", 2L),
                new FolderResponse(4L, "형제루트", null)));

        List<FolderTreeNode> tree = organizeService.getCurrentFolderTree(USER);

        assertThat(tree).hasSize(2);
        FolderTreeNode root = tree.stream().filter(n -> n.folderId().equals(1L)).findFirst().orElseThrow();
        assertThat(root.children()).hasSize(1);
        assertThat(root.children().get(0).folderId()).isEqualTo(2L);
        assertThat(root.children().get(0).children()).hasSize(1);
        assertThat(root.children().get(0).children().get(0).folderId()).isEqualTo(3L);
    }

    // ---- 매핑 검증 ----

    @Test
    void applyProposal_존재하지않는_targetFolderId면_예외() {
        when(fileRepository.findByIdAndDeletedAtIsNull(10L)).thenReturn(Optional.of(ownedFile(10L)));
        when(folderRepository.findByIdAndUsersId(99L, USER)).thenReturn(Optional.empty());

        OrganizeProposal proposal = new OrganizeProposal(
                List.of(),
                List.of(new FileMapping(10L, 99L, null, null)));

        assertThatThrownBy(() -> organizeService.applyProposal(USER, proposal))
                .isInstanceOf(FolderNotFoundException.class);
        verify(fileService, never()).moveToFolder(any(), any(), any());
    }

    @Test
    void applyProposal_다른사람_파일이면_예외() {
        File others = new File();
        others.setId(10L);
        others.setUsersId(OTHER_USER);
        when(fileRepository.findByIdAndDeletedAtIsNull(10L)).thenReturn(Optional.of(others));

        OrganizeProposal proposal = new OrganizeProposal(
                List.of(),
                List.of(new FileMapping(10L, null, null, null)));

        assertThatThrownBy(() -> organizeService.applyProposal(USER, proposal))
                .isInstanceOf(ForbiddenException.class);
        verify(fileService, never()).moveToFolder(any(), any(), any());
    }

    @Test
    void applyProposal_존재하지않는_파일이면_예외() {
        when(fileRepository.findByIdAndDeletedAtIsNull(10L)).thenReturn(Optional.empty());

        OrganizeProposal proposal = new OrganizeProposal(
                List.of(),
                List.of(new FileMapping(10L, null, null, null)));

        assertThatThrownBy(() -> organizeService.applyProposal(USER, proposal))
                .isInstanceOf(FileNotFoundException.class);
    }

    @Test
    void applyProposal_targetFolderId와_targetTempId_동시지정이면_예외() {
        when(fileRepository.findByIdAndDeletedAtIsNull(10L)).thenReturn(Optional.of(ownedFile(10L)));

        OrganizeProposal proposal = new OrganizeProposal(
                List.of(new ProposedFolder("t1", "새 폴더", null, null)),
                List.of(new FileMapping(10L, 5L, "t1", null)));

        assertThatThrownBy(() -> organizeService.applyProposal(USER, proposal))
                .isInstanceOf(BadRequestException.class);
        verifyNoInteractions(folderService);
    }

    @Test
    void applyProposal_존재하지않는_targetTempId_참조시_예외() {
        when(fileRepository.findByIdAndDeletedAtIsNull(10L)).thenReturn(Optional.of(ownedFile(10L)));

        OrganizeProposal proposal = new OrganizeProposal(
                List.of(),
                List.of(new FileMapping(10L, null, "없는temp", null)));

        assertThatThrownBy(() -> organizeService.applyProposal(USER, proposal))
                .isInstanceOf(BadRequestException.class);
    }

    @Test
    void applyProposal_같은파일에_중복매핑이면_예외() {
        when(fileRepository.findByIdAndDeletedAtIsNull(10L)).thenReturn(Optional.of(ownedFile(10L)));

        OrganizeProposal proposal = new OrganizeProposal(
                List.of(),
                List.of(new FileMapping(10L, null, null, null),
                        new FileMapping(10L, null, null, "다른이름")));

        assertThatThrownBy(() -> organizeService.applyProposal(USER, proposal))
                .isInstanceOf(BadRequestException.class);
    }

    @Test
    void applyProposal_confidence가_0보다작거나_1보다크면_예외() {
        when(fileRepository.findByIdAndDeletedAtIsNull(10L)).thenReturn(Optional.of(ownedFile(10L)));

        OrganizeProposal proposal = new OrganizeProposal(
                List.of(),
                List.of(new FileMapping(10L, null, null, null, 1.5)));

        assertThatThrownBy(() -> organizeService.applyProposal(USER, proposal))
                .isInstanceOf(BadRequestException.class);
        verify(fileService, never()).moveToFolder(any(), any(), any());
    }

    // ---- 새 폴더 제안 검증 ----

    @Test
    void applyProposal_새폴더_tempId_중복이면_예외() {
        OrganizeProposal proposal = new OrganizeProposal(
                List.of(new ProposedFolder("t1", "A", null, null),
                        new ProposedFolder("t1", "B", null, null)),
                List.of());

        assertThatThrownBy(() -> organizeService.applyProposal(USER, proposal))
                .isInstanceOf(BadRequestException.class);
    }

    @Test
    void applyProposal_새폴더_parentTempId와_parentFolderId_동시지정이면_예외() {
        OrganizeProposal proposal = new OrganizeProposal(
                List.of(new ProposedFolder("t1", "A", null, null),
                        new ProposedFolder("t2", "B", "t1", 5L)),
                List.of());

        assertThatThrownBy(() -> organizeService.applyProposal(USER, proposal))
                .isInstanceOf(BadRequestException.class);
    }

    @Test
    void applyProposal_존재하지않는_parentTempId_참조시_예외() {
        OrganizeProposal proposal = new OrganizeProposal(
                List.of(new ProposedFolder("t1", "A", "없는temp", null)),
                List.of());

        assertThatThrownBy(() -> organizeService.applyProposal(USER, proposal))
                .isInstanceOf(BadRequestException.class);
    }

    @Test
    void applyProposal_새폴더끼리_순환참조면_예외() {
        OrganizeProposal proposal = new OrganizeProposal(
                List.of(new ProposedFolder("t1", "A", "t2", null),
                        new ProposedFolder("t2", "B", "t1", null)),
                List.of());

        assertThatThrownBy(() -> organizeService.applyProposal(USER, proposal))
                .isInstanceOf(BadRequestException.class);
        verifyNoInteractions(folderService);
    }

    // ---- 정상 반영 ----

    @Test
    void applyProposal_기존폴더로_이동하고_이름도_변경() {
        when(fileRepository.findByIdAndDeletedAtIsNull(10L)).thenReturn(Optional.of(ownedFile(10L)));
        when(folderRepository.findByIdAndUsersId(5L, USER)).thenReturn(Optional.of(new Folder()));

        OrganizeProposal proposal = new OrganizeProposal(
                List.of(),
                List.of(new FileMapping(10L, 5L, null, "새 이름")));

        organizeService.applyProposal(USER, proposal);

        verify(fileService).moveToFolder(USER, 10L, 5L);
        verify(fileService).rename(USER, 10L, "새 이름");
        verifyNoInteractions(folderService);
    }

    @Test
    void applyProposal_새폴더를_만들고_그리로_이동() {
        when(fileRepository.findByIdAndDeletedAtIsNull(10L)).thenReturn(Optional.of(ownedFile(10L)));
        when(folderService.create(USER, "제안폴더", null)).thenReturn(100L);

        OrganizeProposal proposal = new OrganizeProposal(
                List.of(new ProposedFolder("t1", "제안폴더", null, null)),
                List.of(new FileMapping(10L, null, "t1", null)));

        organizeService.applyProposal(USER, proposal);

        verify(folderService).create(USER, "제안폴더", null);
        verify(fileService).moveToFolder(USER, 10L, 100L);
    }

    @Test
    void applyProposal_부모자식_새폴더가_순서대로_생성됨() {
        when(fileRepository.findByIdAndDeletedAtIsNull(10L)).thenReturn(Optional.of(ownedFile(10L)));
        when(folderService.create(USER, "부모", null)).thenReturn(10L);
        when(folderService.create(eq(USER), eq("자식"), eq(10L))).thenReturn(20L);

        OrganizeProposal proposal = new OrganizeProposal(
                List.of(new ProposedFolder("parent", "부모", null, null),
                        new ProposedFolder("child", "자식", "parent", null)),
                List.of(new FileMapping(10L, null, "child", null)));

        organizeService.applyProposal(USER, proposal);

        verify(folderService).create(USER, "부모", null);
        verify(folderService).create(USER, "자식", 10L);
        verify(fileService).moveToFolder(USER, 10L, 20L);
    }

    @Test
    void applyProposal_새폴더의_부모가_기존폴더면_그아래로_생성() {
        when(fileRepository.findByIdAndDeletedAtIsNull(10L)).thenReturn(Optional.of(ownedFile(10L)));
        when(folderRepository.findByIdAndUsersId(5L, USER)).thenReturn(Optional.of(new Folder()));
        when(folderService.create(USER, "제안폴더", 5L)).thenReturn(100L);

        OrganizeProposal proposal = new OrganizeProposal(
                List.of(new ProposedFolder("t1", "제안폴더", null, 5L)),
                List.of(new FileMapping(10L, null, "t1", null)));

        organizeService.applyProposal(USER, proposal);

        verify(folderService).create(USER, "제안폴더", 5L);
        verify(fileService).moveToFolder(USER, 10L, 100L);
    }

    // ---- confidence 기반 자동 적용 필터링 ----

    @Test
    void applyProposal_confidence가_민감도_미만이면_보류되고_이동하지않는다() {
        givenSensitivity("0.500");
        when(fileRepository.findByIdAndDeletedAtIsNull(10L)).thenReturn(Optional.of(ownedFile(10L)));
        when(folderRepository.findByIdAndUsersId(5L, USER)).thenReturn(Optional.of(new Folder()));

        OrganizeProposal proposal = new OrganizeProposal(
                List.of(),
                List.of(new FileMapping(10L, 5L, null, null, 0.4)));

        OrganizeApplyResponse response = organizeService.applyProposal(USER, proposal);

        verify(fileService, never()).moveToFolder(any(), any(), any());
        assertThat(response.success()).isTrue();
        assertThat(response.held()).hasSize(1);
        assertThat(response.held().get(0).fileId()).isEqualTo(10L);
    }

    @Test
    void applyProposal_confidence가_민감도_이상이면_그대로_적용된다() {
        givenSensitivity("0.500");
        when(fileRepository.findByIdAndDeletedAtIsNull(10L)).thenReturn(Optional.of(ownedFile(10L)));
        when(folderRepository.findByIdAndUsersId(5L, USER)).thenReturn(Optional.of(new Folder()));

        OrganizeProposal proposal = new OrganizeProposal(
                List.of(),
                List.of(new FileMapping(10L, 5L, null, null, 0.92)));

        OrganizeApplyResponse response = organizeService.applyProposal(USER, proposal);

        verify(fileService).moveToFolder(USER, 10L, 5L);
        assertThat(response.held()).isEmpty();
    }

    @Test
    void applyProposal_confidence가_없으면_필터링없이_그대로_적용된다() {
        when(fileRepository.findByIdAndDeletedAtIsNull(10L)).thenReturn(Optional.of(ownedFile(10L)));
        when(folderRepository.findByIdAndUsersId(5L, USER)).thenReturn(Optional.of(new Folder()));

        OrganizeProposal proposal = new OrganizeProposal(
                List.of(),
                List.of(new FileMapping(10L, 5L, null, null)));

        OrganizeApplyResponse response = organizeService.applyProposal(USER, proposal);

        verify(fileService).moveToFolder(USER, 10L, 5L);
        assertThat(response.held()).isEmpty();
        // confidence가 아예 없는 매핑뿐이면 민감도 조회 자체를 하지 않는다.
        verifyNoInteractions(userSettingService);
    }

    @Test
    void applyProposal_매핑전부보류되면_그새폴더는_생성되지않는다() {
        givenSensitivity("0.500");
        when(fileRepository.findByIdAndDeletedAtIsNull(10L)).thenReturn(Optional.of(ownedFile(10L)));

        OrganizeProposal proposal = new OrganizeProposal(
                List.of(new ProposedFolder("t1", "제안폴더", null, null)),
                List.of(new FileMapping(10L, null, "t1", null, 0.1)));

        OrganizeApplyResponse response = organizeService.applyProposal(USER, proposal);

        verifyNoInteractions(folderService);
        verify(fileService, never()).moveToFolder(any(), any(), any());
        assertThat(response.held()).hasSize(1);
    }

    @Test
    void applyProposal_일부매핑만보류돼도_적용되는매핑이_쓰는새폴더는_생성된다() {
        givenSensitivity("0.500");
        when(fileRepository.findByIdAndDeletedAtIsNull(10L)).thenReturn(Optional.of(ownedFile(10L)));
        when(fileRepository.findByIdAndDeletedAtIsNull(11L)).thenReturn(Optional.of(ownedFile(11L)));
        when(folderService.create(USER, "제안폴더", null)).thenReturn(100L);

        OrganizeProposal proposal = new OrganizeProposal(
                List.of(new ProposedFolder("t1", "제안폴더", null, null)),
                List.of(new FileMapping(10L, null, "t1", null, 0.9),
                        new FileMapping(11L, null, "t1", null, 0.1)));

        OrganizeApplyResponse response = organizeService.applyProposal(USER, proposal);

        verify(folderService).create(USER, "제안폴더", null);
        verify(fileService).moveToFolder(USER, 10L, 100L);
        verify(fileService, never()).moveToFolder(eq(USER), eq(11L), any());
        assertThat(response.held()).hasSize(1);
        assertThat(response.held().get(0).fileId()).isEqualTo(11L);
    }

    // ---- AI 제안 생성 ----

    @Test
    void generateProposal_AI_제안을_검증해서_반환한다() {
        when(folderService.list(USER)).thenReturn(List.of());
        when(organizeInputAssembler.assemble(USER)).thenReturn(List.of());
        when(fileRepository.findByIdAndDeletedAtIsNull(10L)).thenReturn(Optional.of(ownedFile(10L)));

        OrganizeProposal aiResponse = new OrganizeProposal(
                List.of(new ProposedFolder("t1", "제안폴더", null, null)),
                List.of(new FileMapping(10L, null, "t1", null)));
        when(aiOrganizeClient.proposeOrganization(any(), any())).thenReturn(aiResponse);

        OrganizeProposal result = organizeService.generateProposal(USER);

        assertThat(result).isEqualTo(aiResponse);
        // getCurrentFolderTree 때문에 folderService.list는 호출되지만, 실제 반영(새 폴더 생성/
        // 파일 이동)은 하지 않아야 한다.
        verify(folderService, never()).create(any(), any(), any());
        verify(fileService, never()).moveToFolder(any(), any(), any());
    }

    @Test
    void generateProposal_AI가_존재하지않는_파일을_참조하면_예외() {
        when(folderService.list(USER)).thenReturn(List.of());
        when(organizeInputAssembler.assemble(USER)).thenReturn(List.of());
        when(fileRepository.findByIdAndDeletedAtIsNull(999L)).thenReturn(Optional.empty());

        OrganizeProposal aiResponse = new OrganizeProposal(
                List.of(),
                List.of(new FileMapping(999L, null, null, null)));
        when(aiOrganizeClient.proposeOrganization(any(), any())).thenReturn(aiResponse);

        assertThatThrownBy(() -> organizeService.generateProposal(USER))
                .isInstanceOf(FileNotFoundException.class);
    }

    @Test
    void generateProposal_newFolders_mappings가_null이어도_안전하게_처리() {
        when(folderService.list(USER)).thenReturn(List.of());
        when(organizeInputAssembler.assemble(USER)).thenReturn(List.of());
        when(aiOrganizeClient.proposeOrganization(any(), any()))
                .thenReturn(new OrganizeProposal(null, null));

        OrganizeProposal result = organizeService.generateProposal(USER);

        assertThat(result.newFolders()).isEmpty();
        assertThat(result.mappings()).isEmpty();
    }

    // ---- 재시도 중복 반영 방지(idempotencyKey) ----

    @Test
    void applyProposal_같은_idempotencyKey로_재요청하면_두번째_반영은_조용히_무시된다() {
        when(fileRepository.findByIdAndDeletedAtIsNull(10L)).thenReturn(Optional.of(ownedFile(10L)));
        when(folderService.create(USER, "제안폴더", null)).thenReturn(100L);

        OrganizeProposal proposal = new OrganizeProposal(
                List.of(new ProposedFolder("t1", "제안폴더", null, null)),
                List.of(new FileMapping(10L, null, "t1", null)),
                "idem-key-1");

        organizeService.applyProposal(USER, proposal);
        organizeService.applyProposal(USER, proposal);

        // 새 폴더가 또 만들어지거나 파일이 다시 이동되면 안 된다 — 두 번째 호출은 조용히 무시되어야 한다.
        verify(folderService, times(1)).create(USER, "제안폴더", null);
        verify(fileService, times(1)).moveToFolder(USER, 10L, 100L);
    }

    @Test
    void applyProposal_idempotencyKey가_없으면_매번_새로_반영된다() {
        when(fileRepository.findByIdAndDeletedAtIsNull(10L)).thenReturn(Optional.of(ownedFile(10L)));
        when(folderRepository.findByIdAndUsersId(5L, USER)).thenReturn(Optional.of(new Folder()));

        // 2-인자 생성자 사용 — idempotencyKey가 없는 기존 호출부(테스트 포함) 호환 케이스.
        OrganizeProposal proposal = new OrganizeProposal(
                List.of(),
                List.of(new FileMapping(10L, 5L, null, null)));

        organizeService.applyProposal(USER, proposal);
        organizeService.applyProposal(USER, proposal);

        verify(fileService, times(2)).moveToFolder(USER, 10L, 5L);
    }

    @Test
    void applyProposal_idempotencyKey가_빈문자열이면_없는것과_동일하게_매번_반영된다() {
        when(fileRepository.findByIdAndDeletedAtIsNull(10L)).thenReturn(Optional.of(ownedFile(10L)));
        when(folderRepository.findByIdAndUsersId(5L, USER)).thenReturn(Optional.of(new Folder()));

        OrganizeProposal proposal = new OrganizeProposal(
                List.of(),
                List.of(new FileMapping(10L, 5L, null, null)),
                "   ");

        organizeService.applyProposal(USER, proposal);
        organizeService.applyProposal(USER, proposal);

        verify(fileService, times(2)).moveToFolder(USER, 10L, 5L);
    }

    @Test
    void applyProposal_다른_idempotencyKey면_둘_다_반영된다() {
        when(fileRepository.findByIdAndDeletedAtIsNull(10L)).thenReturn(Optional.of(ownedFile(10L)));
        when(folderRepository.findByIdAndUsersId(5L, USER)).thenReturn(Optional.of(new Folder()));

        OrganizeProposal first = new OrganizeProposal(
                List.of(), List.of(new FileMapping(10L, 5L, null, null)), "key-1");
        OrganizeProposal second = new OrganizeProposal(
                List.of(), List.of(new FileMapping(10L, 5L, null, null)), "key-2");

        organizeService.applyProposal(USER, first);
        organizeService.applyProposal(USER, second);

        verify(fileService, times(2)).moveToFolder(USER, 10L, 5L);
    }

    @Test
    void applyProposal_다른_사용자가_같은_idempotencyKey를_보내도_각각_반영된다() {
        // 캐시 키는 userId:idempotencyKey 조합이라 사용자가 다르면 충돌하지 않아야 한다.
        File otherUsersFile = new File();
        otherUsersFile.setId(20L);
        otherUsersFile.setUsersId(OTHER_USER);
        when(fileRepository.findByIdAndDeletedAtIsNull(10L)).thenReturn(Optional.of(ownedFile(10L)));
        when(fileRepository.findByIdAndDeletedAtIsNull(20L)).thenReturn(Optional.of(otherUsersFile));
        when(folderRepository.findByIdAndUsersId(5L, USER)).thenReturn(Optional.of(new Folder()));
        when(folderRepository.findByIdAndUsersId(5L, OTHER_USER)).thenReturn(Optional.of(new Folder()));

        OrganizeProposal proposalForUser = new OrganizeProposal(
                List.of(), List.of(new FileMapping(10L, 5L, null, null)), "shared-key");
        OrganizeProposal proposalForOtherUser = new OrganizeProposal(
                List.of(), List.of(new FileMapping(20L, 5L, null, null)), "shared-key");

        organizeService.applyProposal(USER, proposalForUser);
        organizeService.applyProposal(OTHER_USER, proposalForOtherUser);

        verify(fileService).moveToFolder(USER, 10L, 5L);
        verify(fileService).moveToFolder(OTHER_USER, 20L, 5L);
    }
}
