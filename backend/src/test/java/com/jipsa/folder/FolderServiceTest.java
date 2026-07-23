package com.jipsa.folder;

import com.jipsa.common.BadRequestException;
import com.jipsa.file.File;
import com.jipsa.file.FileRepository;
import com.jipsa.file.FileStatus;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * H2(create-drop) 위에서 FolderService를 직접 검증한다.
 * 각 테스트는 @Transactional로 감싸져 끝나면 롤백되므로 테스트 간 데이터가 섞이지 않는다.
 */
@SpringBootTest
@Transactional
class FolderServiceTest {

    @Autowired
    private FolderService folderService;

    @Autowired
    private FolderRepository folderRepository;

    @Autowired
    private FileRepository fileRepository;

    private static final Long USER = 1L;
    private static final Long OTHER_USER = 2L;
    private static final Long NON_EXISTENT_ID = 999_999_999L;

    private File fileIn(Long folderId) {
        File file = new File();
        file.setUsersId(USER);
        file.setFolderId(folderId);
        file.setName("문서.pdf");
        file.setS3Key("files/" + UUID.randomUUID());
        file.setFileType("pdf");
        return fileRepository.save(file);
    }

    @Test
    void create_루트폴더_생성() {
        Long id = folderService.create(USER, "재무 보고서", null);

        Folder saved = folderRepository.findById(id).orElseThrow();
        assertThat(saved.getName()).isEqualTo("재무 보고서");
        assertThat(saved.getParentFolderId()).isNull();
        assertThat(saved.getUsersId()).isEqualTo(USER);
    }

    @Test
    void create_이름이_공백이면_예외() {
        assertThatThrownBy(() -> folderService.create(USER, "   ", null))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void create_존재하지않는_부모지정시_예외() {
        assertThatThrownBy(() -> folderService.create(USER, "폴더", NON_EXISTENT_ID))
                .isInstanceOf(FolderNotFoundException.class);
    }

    @Test
    void create_다른사람_폴더를_부모로_지정하면_예외() {
        Long otherFolderId = folderService.create(OTHER_USER, "남의 폴더", null);

        assertThatThrownBy(() -> folderService.create(USER, "내 폴더", otherFolderId))
                .isInstanceOf(FolderNotFoundException.class);
    }

    @Test
    void list_본인_폴더만_반환() {
        folderService.create(USER, "A", null);
        folderService.create(USER, "B", null);
        folderService.create(OTHER_USER, "남의 폴더", null);

        List<FolderResponse> result = folderService.list(USER);

        assertThat(result).hasSize(2)
                .extracting(FolderResponse::name)
                .containsExactlyInAnyOrder("A", "B");
    }

    @Test
    void update_이름만_변경() {
        Long id = folderService.create(USER, "원래이름", null);

        folderService.update(USER, id, "바뀐이름", true, null, false);

        Folder updated = folderRepository.findById(id).orElseThrow();
        assertThat(updated.getName()).isEqualTo("바뀐이름");
    }

    @Test
    void update_parentFolderId_필드를_안보내면_부모_유지() {
        Long parentId = folderService.create(USER, "부모", null);
        Long childId = folderService.create(USER, "자식", parentId);

        folderService.update(USER, childId, "자식-이름변경", true, null, false);

        Folder child = folderRepository.findById(childId).orElseThrow();
        assertThat(child.getParentFolderId()).isEqualTo(parentId);
    }

    @Test
    void update_parentFolderId_null을_명시하면_루트로_이동() {
        Long parentId = folderService.create(USER, "부모", null);
        Long childId = folderService.create(USER, "자식", parentId);

        folderService.update(USER, childId, null, false, null, true);

        Folder child = folderRepository.findById(childId).orElseThrow();
        assertThat(child.getParentFolderId()).isNull();
    }

    @Test
    void update_자기자신을_부모로_지정하면_예외() {
        Long id = folderService.create(USER, "폴더", null);

        assertThatThrownBy(() -> folderService.update(USER, id, null, false, id, true))
                .isInstanceOf(FolderCircularReferenceException.class);
    }

    @Test
    void update_자신의_자손을_부모로_지정하면_예외() {
        Long grandparent = folderService.create(USER, "조부모", null);
        Long parent = folderService.create(USER, "부모", grandparent);
        Long child = folderService.create(USER, "자식", parent);

        // 조부모를 자신의 자손(child) 아래로 옮기려는 시도 -> 순환 참조
        assertThatThrownBy(() -> folderService.update(USER, grandparent, null, false, child, true))
                .isInstanceOf(FolderCircularReferenceException.class);
    }

    @Test
    void update_다른사람_폴더면_예외() {
        Long otherFolderId = folderService.create(OTHER_USER, "남의 폴더", null);

        assertThatThrownBy(() -> folderService.update(USER, otherFolderId, "이름변경", true, null, false))
                .isInstanceOf(FolderNotFoundException.class);
    }

    @Test
    void update_존재하지않는_부모로_이동하면_예외() {
        Long id = folderService.create(USER, "폴더", null);

        assertThatThrownBy(() -> folderService.update(USER, id, null, false, NON_EXISTENT_ID, true))
                .isInstanceOf(FolderNotFoundException.class);
    }

    @Test
    void delete_자손폴더까지_함께_소프트삭제() {
        Long root = folderService.create(USER, "루트", null);
        Long child1 = folderService.create(USER, "자식1", root);
        Long child2 = folderService.create(USER, "자식2", root);
        Long grandchild = folderService.create(USER, "손자", child1);

        folderService.delete(USER, root);

        assertThat(folderRepository.findById(root).orElseThrow().getDeletedAt()).isNotNull();
        assertThat(folderRepository.findById(child1).orElseThrow().getDeletedAt()).isNotNull();
        assertThat(folderRepository.findById(child2).orElseThrow().getDeletedAt()).isNotNull();
        assertThat(folderRepository.findById(grandchild).orElseThrow().getDeletedAt()).isNotNull();
    }

    @Test
    void delete_이후_목록조회에서_제외() {
        Long root = folderService.create(USER, "루트", null);

        folderService.delete(USER, root);

        assertThat(folderService.list(USER)).extracting(FolderResponse::folderId).doesNotContain(root);
    }

    @Test
    void delete_하위파일도_함께_휴지통으로_이동() {
        Long root = folderService.create(USER, "루트", null);
        File file = fileIn(root);

        folderService.delete(USER, root);

        File reloaded = fileRepository.findById(file.getId()).orElseThrow();
        assertThat(reloaded.getDeletedAt()).isNotNull();
    }

    @Test
    void delete_다른가지는_영향없음() {
        Long root = folderService.create(USER, "루트", null);
        Long child = folderService.create(USER, "자식", root);
        Long sibling = folderService.create(USER, "형제루트", null);

        folderService.delete(USER, child);

        assertThat(folderRepository.findById(child).orElseThrow().getDeletedAt()).isNotNull();
        assertThat(folderRepository.findById(root).orElseThrow().getDeletedAt()).isNull();
        assertThat(folderRepository.findById(sibling).orElseThrow().getDeletedAt()).isNull();
    }

    @Test
    void delete_다른사람_폴더면_예외() {
        Long otherFolderId = folderService.create(OTHER_USER, "남의 폴더", null);

        assertThatThrownBy(() -> folderService.delete(USER, otherFolderId))
                .isInstanceOf(FolderNotFoundException.class);
    }

    @Test
    void delete_이미삭제된_폴더면_존재하지않는것으로_처리() {
        Long id = folderService.create(USER, "폴더", null);
        folderService.delete(USER, id);

        assertThatThrownBy(() -> folderService.delete(USER, id))
                .isInstanceOf(FolderNotFoundException.class);
    }

    @Test
    void restore_삭제된_폴더와_자손_하위파일까지_함께_복원() {
        Long root = folderService.create(USER, "루트", null);
        Long child = folderService.create(USER, "자식", root);
        File file = fileIn(child);

        folderService.delete(USER, root);
        folderService.restore(USER, root);

        assertThat(folderRepository.findById(root).orElseThrow().getDeletedAt()).isNull();
        assertThat(folderRepository.findById(child).orElseThrow().getDeletedAt()).isNull();
        assertThat(fileRepository.findById(file.getId()).orElseThrow().getDeletedAt()).isNull();
        assertThat(folderService.list(USER)).extracting(FolderResponse::folderId).contains(root, child);
    }

    @Test
    void restore_폴더삭제_이전에_따로_지워진_파일은_함께_복원되지_않는다() {
        Long root = folderService.create(USER, "루트", null);
        File file = fileIn(root);
        file.setStatus(FileStatus.DELETED);
        file.setDeletedAt(LocalDateTime.now().minusDays(1));
        fileRepository.save(file);

        folderService.delete(USER, root);
        folderService.restore(USER, root);

        assertThat(folderRepository.findById(root).orElseThrow().getDeletedAt()).isNull();
        assertThat(fileRepository.findById(file.getId()).orElseThrow().getDeletedAt()).isNotNull();
    }

    @Test
    void restore_부모가_삭제상태로_남아있으면_자식복원시_루트로_이동() {
        Long root = folderService.create(USER, "루트", null);
        Long child = folderService.create(USER, "자식", root);

        folderService.delete(USER, root);
        folderService.restore(USER, child);

        Folder restoredChild = folderRepository.findById(child).orElseThrow();
        assertThat(restoredChild.getDeletedAt()).isNull();
        assertThat(restoredChild.getParentFolderId()).isNull();
        assertThat(folderRepository.findById(root).orElseThrow().getDeletedAt()).isNotNull();
    }

    @Test
    void restore_삭제되지_않은_폴더면_예외() {
        Long id = folderService.create(USER, "폴더", null);

        assertThatThrownBy(() -> folderService.restore(USER, id))
                .isInstanceOf(BadRequestException.class);
    }

    @Test
    void restore_다른사람_폴더면_예외() {
        Long otherFolderId = folderService.create(OTHER_USER, "남의 폴더", null);

        assertThatThrownBy(() -> folderService.restore(USER, otherFolderId))
                .isInstanceOf(FolderNotFoundException.class);
    }

    @Test
    void listTrash_삭제된_폴더만_반환() {
        Long active = folderService.create(USER, "활성", null);
        Long deleted = folderService.create(USER, "삭제됨", null);
        folderService.delete(USER, deleted);

        FolderTrashListResponse result = folderService.listTrash(USER, 0);

        assertThat(result.folders()).extracting(FolderResponse::folderId)
                .contains(deleted)
                .doesNotContain(active);
        assertThat(result.total()).isEqualTo(1);
    }
}
