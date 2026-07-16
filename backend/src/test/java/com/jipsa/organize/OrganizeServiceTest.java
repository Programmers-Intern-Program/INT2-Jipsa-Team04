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
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
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

    private OrganizeService organizeService;

    private static final Long USER = 1L;
    private static final Long OTHER_USER = 2L;

    @BeforeEach
    void setUp() {
        organizeService = new OrganizeService(folderRepository, fileRepository, folderService, fileService,
                organizeInputAssembler, aiOrganizeClient);
    }

    private File ownedFile(Long id) {
        File file = new File();
        file.setId(id);
        file.setUsersId(USER);
        return file;
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
}
