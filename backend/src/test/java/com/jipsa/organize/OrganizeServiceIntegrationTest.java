package com.jipsa.organize;

import com.jipsa.common.exception.ForbiddenException;
import com.jipsa.file.File;
import com.jipsa.file.FileRepository;
import com.jipsa.folder.Folder;
import com.jipsa.folder.FolderRepository;
import com.jipsa.folder.FolderService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * OrganizeService를 H2(create-drop) 위에서 실제 FolderService/FileService/Repository와 함께
 * 통합 검증한다(FolderServiceTest와 같은 방식). Mockito 단위 테스트(OrganizeServiceTest)는
 * 로직 분기를 촘촘히 검증하고, 여기서는 "실제로 DB에 반영되는지"와 "실패 시 아무것도
 * 반영되지 않는지(all-or-nothing)"를 확인한다. 각 테스트는 @Transactional로 끝나면 롤백된다.
 */
@SpringBootTest
@Transactional
class OrganizeServiceIntegrationTest {

    @Autowired
    private OrganizeService organizeService;

    @Autowired
    private FolderService folderService;

    @Autowired
    private FolderRepository folderRepository;

    @Autowired
    private FileRepository fileRepository;

    private static final Long USER = 1L;
    private static final Long OTHER_USER = 2L;

    private File saveFile(Long userId, String name, Long folderId) {
        File file = new File();
        file.setUsersId(userId);
        file.setName(name);
        file.setS3Key("files/" + UUID.randomUUID());
        file.setFileType("pdf");
        file.setSizeBytes(100L);
        file.setFolderId(folderId);
        return fileRepository.save(file);
    }

    @Test
    void getCurrentFolderTree_실제_저장된_폴더로_트리를_조립한다() {
        Long root = folderService.create(USER, "루트", null);
        Long child = folderService.create(USER, "자식", root);
        folderService.create(OTHER_USER, "남의 루트", null);

        List<FolderTreeNode> tree = organizeService.getCurrentFolderTree(USER);

        assertThat(tree).hasSize(1);
        assertThat(tree.get(0).folderId()).isEqualTo(root);
        assertThat(tree.get(0).children()).hasSize(1);
        assertThat(tree.get(0).children().get(0).folderId()).isEqualTo(child);
    }

    @Test
    void applyProposal_새폴더_생성과_파일_이동_이름변경이_실제로_반영된다() {
        File file = saveFile(USER, "원본이름.pdf", null);

        OrganizeProposal proposal = new OrganizeProposal(
                List.of(new ProposedFolder("t1", "제안폴더", null, null)),
                List.of(new FileMapping(file.getId(), null, "t1", "새이름.pdf")));

        organizeService.applyProposal(USER, proposal);

        List<Folder> created = folderRepository.findByUsersId(USER);
        assertThat(created).hasSize(1);
        Folder createdFolder = created.get(0);
        assertThat(createdFolder.getName()).isEqualTo("제안폴더");
        assertThat(createdFolder.getParentFolderId()).isNull();

        File updated = fileRepository.findById(file.getId()).orElseThrow();
        assertThat(updated.getFolderId()).isEqualTo(createdFolder.getId());
        assertThat(updated.getName()).isEqualTo("새이름.pdf");

        // 미리보기 트리에도 방금 만든 폴더가 바로 반영되는지 확인.
        List<FolderTreeNode> tree = organizeService.getCurrentFolderTree(USER);
        assertThat(tree).hasSize(1);
        assertThat(tree.get(0).folderId()).isEqualTo(createdFolder.getId());
    }

    @Test
    void applyProposal_부모자식_새폴더가_실제_부모관계로_저장된다() {
        File file = saveFile(USER, "파일.pdf", null);

        OrganizeProposal proposal = new OrganizeProposal(
                List.of(new ProposedFolder("parent", "부모", null, null),
                        new ProposedFolder("child", "자식", "parent", null)),
                List.of(new FileMapping(file.getId(), null, "child", null)));

        organizeService.applyProposal(USER, proposal);

        List<Folder> created = folderRepository.findByUsersId(USER);
        assertThat(created).hasSize(2);
        Folder parent = created.stream().filter(f -> f.getName().equals("부모")).findFirst().orElseThrow();
        Folder child = created.stream().filter(f -> f.getName().equals("자식")).findFirst().orElseThrow();
        assertThat(child.getParentFolderId()).isEqualTo(parent.getId());

        File updated = fileRepository.findById(file.getId()).orElseThrow();
        assertThat(updated.getFolderId()).isEqualTo(child.getId());
    }

    @Test
    void applyProposal_매핑_하나가_실패하면_다른_유효한_매핑도_반영되지_않는다() {
        Long folderA = folderService.create(USER, "폴더A", null);
        File myFile = saveFile(USER, "내파일.pdf", null);
        File othersFile = saveFile(OTHER_USER, "남의파일.pdf", null);

        OrganizeProposal proposal = new OrganizeProposal(
                List.of(),
                List.of(new FileMapping(myFile.getId(), folderA, null, null),
                        new FileMapping(othersFile.getId(), folderA, null, null)));

        assertThatThrownBy(() -> organizeService.applyProposal(USER, proposal))
                .isInstanceOf(ForbiddenException.class);

        // 앞선 매핑(myFile)이 유효했더라도, 뒤 매핑(othersFile)이 검증에서 걸리면서
        // 아무것도 반영되지 않아야 한다(all-or-nothing).
        File reloaded = fileRepository.findById(myFile.getId()).orElseThrow();
        assertThat(reloaded.getFolderId()).isNull();
    }

    @Test
    void applyProposal_검증실패시_새폴더도_생성되지_않는다() {
        Long nonExistentFileId = 999_999_999L;

        OrganizeProposal proposal = new OrganizeProposal(
                List.of(new ProposedFolder("t1", "롤백테스트", null, null)),
                List.of(new FileMapping(nonExistentFileId, null, "t1", null)));

        assertThatThrownBy(() -> organizeService.applyProposal(USER, proposal))
                .isInstanceOf(RuntimeException.class);

        assertThat(folderRepository.findByUsersId(USER))
                .extracting(Folder::getName)
                .doesNotContain("롤백테스트");
    }
}
