package com.jipsa.folder;

import com.jipsa.common.BadRequestException;
import com.jipsa.file.FileService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * FolderService.permanentDelete()는 FileService를 통해 S3 실물까지 정리하는데,
 * S3Service는 실제 S3Client를 사용하는 빈이라(@SpringBootTest에서는 네트워크 호출 위험) 여기서는
 * FolderServiceTest(@SpringBootTest)와 분리된 순수 Mockito 단위 테스트로 검증한다.
 */
@ExtendWith(MockitoExtension.class)
class FolderServicePermanentDeleteTest {

    @Mock
    private FolderRepository folderRepository;
    @Mock
    private FileService fileService;

    private FolderService folderService;

    private static final Long USER = 1L;

    @BeforeEach
    void setUp() {
        folderService = new FolderService(folderRepository, fileService);
    }

    private Folder folder(Long id, Long parentId, LocalDateTime deletedAt) {
        Folder folder = new Folder();
        folder.setId(id);
        folder.setUsersId(USER);
        folder.setParentFolderId(parentId);
        folder.setDeletedAt(deletedAt);
        return folder;
    }

    @Test
    void permanentDelete_자손까지_함께_영구삭제하고_파일도_정리() {
        LocalDateTime deletedAt = LocalDateTime.now();
        Folder root = folder(1L, null, deletedAt);
        Folder child = folder(2L, 1L, deletedAt);
        when(folderRepository.findByIdAndUsersIdIncludingDeleted(1L, USER)).thenReturn(Optional.of(root));
        when(folderRepository.findByUsersIdIncludingDeleted(USER)).thenReturn(List.of(root, child));

        folderService.permanentDelete(USER, 1L);

        ArgumentCaptor<List<Long>> fileServiceIds = ArgumentCaptor.forClass(List.class);
        verify(fileService).permanentDeleteByFolderIds(fileServiceIds.capture());
        assertThat(fileServiceIds.getValue()).containsExactlyInAnyOrder(1L, 2L);

        ArgumentCaptor<List<Long>> deletedIds = ArgumentCaptor.forClass(List.class);
        verify(folderRepository).deleteAllById(deletedIds.capture());
        // 자기참조 FK 제약상 자식(2L)이 부모(1L)보다 먼저 삭제되어야 한다.
        assertThat(deletedIds.getValue()).containsExactly(2L, 1L);
    }

    @Test
    void permanentDelete_휴지통에_없으면_예외() {
        Folder active = folder(1L, null, null);
        when(folderRepository.findByIdAndUsersIdIncludingDeleted(1L, USER)).thenReturn(Optional.of(active));

        assertThatThrownBy(() -> folderService.permanentDelete(USER, 1L))
                .isInstanceOf(BadRequestException.class);
    }

    @Test
    void permanentDelete_다른사람_폴더면_예외() {
        when(folderRepository.findByIdAndUsersIdIncludingDeleted(1L, USER)).thenReturn(Optional.empty());

        assertThatThrownBy(() -> folderService.permanentDelete(USER, 1L))
                .isInstanceOf(FolderNotFoundException.class);
    }

    @Test
    void restore_다른사람_폴더면_예외_단위테스트() {
        when(folderRepository.findByIdAndUsersIdIncludingDeleted(1L, USER)).thenReturn(Optional.empty());

        assertThatThrownBy(() -> folderService.restore(USER, 1L))
                .isInstanceOf(FolderNotFoundException.class);
    }
}
