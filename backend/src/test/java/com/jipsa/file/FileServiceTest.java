package com.jipsa.file;

import com.jipsa.common.BadRequestException;
import com.jipsa.common.exception.FileNotFoundException;
import com.jipsa.common.exception.ForbiddenException;
import com.jipsa.folder.Folder;
import com.jipsa.folder.FolderNotFoundException;
import com.jipsa.folder.FolderRepository;
import com.jipsa.job.JobRepository;
import com.jipsa.job.JobService;
import com.jipsa.purge.RagPurgeService;
import com.jipsa.chunk.ChunkRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.data.domain.PageImpl;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;

import java.time.LocalDateTime;
import java.util.Optional;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.when;
import static org.mockito.Mockito.verify;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;

@ExtendWith(MockitoExtension.class)
class FileServiceTest {

    @Mock
    private FileRepository fileRepository;
    @Mock
    private JobRepository jobRepository;
    @Mock
    private FolderRepository folderRepository;
    @Mock
    private S3Service s3Service;
    @Mock
    private FileMetadataRepository fileMetadataRepository;
    @Mock
    private JobService jobService;
    @Mock
    private ChunkRepository chunkRepository;
    @Mock
    private RagPurgeService ragPurgeService;

    private FileService fileService;

    @BeforeEach
    void setUp() {
        fileService = new FileService(fileRepository, jobRepository, jobService, folderRepository,
                fileMetadataRepository, s3Service, "test-bucket", 1000L, chunkRepository, ragPurgeService);
    }

    private File ownedFile() {
        File file = new File();
        file.setId(1L);
        file.setUsersId(1L);
        return file;
    }

    @Test
    void moveToFolderUpdatesFolderId() {
        File file = ownedFile();
        when(fileRepository.findByIdAndDeletedAtIsNull(1L)).thenReturn(Optional.of(file));
        when(folderRepository.findByIdAndUsersId(5L, 1L)).thenReturn(Optional.of(new Folder()));

        fileService.moveToFolder(1L, 1L, 5L);

        assertThat(file.getFolderId()).isEqualTo(5L);
    }

    @Test
    void moveToRootAcceptsNullFolder() {
        File file = ownedFile();
        file.setFolderId(9L);
        when(fileRepository.findByIdAndDeletedAtIsNull(1L)).thenReturn(Optional.of(file));

        fileService.moveToFolder(1L, 1L, null);

        assertThat(file.getFolderId()).isNull();
    }

    @Test
    void rejectsFileOwnedByAnotherUser() {
        File file = ownedFile();
        file.setUsersId(2L);
        when(fileRepository.findByIdAndDeletedAtIsNull(1L)).thenReturn(Optional.of(file));

        assertThatThrownBy(() -> fileService.moveToFolder(1L, 1L, 5L))
                .isInstanceOf(ForbiddenException.class);
    }

    @Test
    void rejectsMissingFile() {
        when(fileRepository.findByIdAndDeletedAtIsNull(1L)).thenReturn(Optional.empty());

        assertThatThrownBy(() -> fileService.moveToFolder(1L, 1L, 5L))
                .isInstanceOf(FileNotFoundException.class);
    }

    @Test
    void rejectsUnknownOrUnownedFolder() {
        File file = ownedFile();
        when(fileRepository.findByIdAndDeletedAtIsNull(1L)).thenReturn(Optional.of(file));
        when(folderRepository.findByIdAndUsersId(5L, 1L)).thenReturn(Optional.empty());

        assertThatThrownBy(() -> fileService.moveToFolder(1L, 1L, 5L))
                .isInstanceOf(FolderNotFoundException.class);
    }

    @Test
    void setStarUpdatesFlag() {
        File file = ownedFile();
        when(fileRepository.findByIdAndDeletedAtIsNull(1L)).thenReturn(Optional.of(file));

        fileService.setStar(1L, 1L, true);

        assertThat(file.isStar()).isTrue();
    }

    @Test
    void renameUpdatesTrimmedName() {
        File file = ownedFile();
        when(fileRepository.findByIdAndDeletedAtIsNull(1L)).thenReturn(Optional.of(file));

        fileService.rename(1L, 1L, "  새 이름  ");

        assertThat(file.getName()).isEqualTo("새 이름");
    }

    @Test
    void renameRejectsBlank() {
        assertThatThrownBy(() -> fileService.rename(1L, 1L, "   "))
                .isInstanceOf(BadRequestException.class);
    }

    @Test
    void downloadReturnsFileContent() {
        File file = ownedFile();
        file.setName("test.pdf");
        file.setS3Key("files/abc");
        when(fileRepository.findByIdAndDeletedAtIsNull(1L)).thenReturn(Optional.of(file));
        when(s3Service.download("test-bucket", "files/abc"))
                .thenReturn(new S3Service.Content(new ByteArrayResource(new byte[]{1, 2, 3}), "application/pdf", 3L));

        FileDownload result = fileService.download(1L, 1L);

        assertThat(result.filename()).isEqualTo("test.pdf");
        assertThat(result.contentType()).isEqualTo("application/pdf");
        assertThat(result.contentLength()).isEqualTo(3L);
    }

    @Test
    void getDetailReturnsSummaryAndTagsFromMetadata() {
        File file = ownedFile();
        file.setName("doc.pdf");
        FileMetadata metadata = new FileMetadata();
        metadata.setFileId(1L);
        metadata.setSummary("계약 요약");
        metadata.setTags("[\"세금\",\"계약\"]");
        when(fileRepository.findByIdAndDeletedAtIsNull(1L)).thenReturn(Optional.of(file));
        when(fileMetadataRepository.findById(1L)).thenReturn(Optional.of(metadata));

        FileDetailResponse result = fileService.getDetail(1L, 1L);

        assertThat(result.summary()).isEqualTo("계약 요약");
        assertThat(result.tags()).containsExactly("세금", "계약");
        assertThat(result.entities()).isNotNull();
        assertThat(result.entities().dates()).isEmpty();
    }

    @Test
    void getDetailReturnsEmptyMetadataWhenAbsent() {
        File file = ownedFile();
        when(fileRepository.findByIdAndDeletedAtIsNull(1L)).thenReturn(Optional.of(file));
        when(fileMetadataRepository.findById(1L)).thenReturn(Optional.empty());

        FileDetailResponse result = fileService.getDetail(1L, 1L);

        assertThat(result.summary()).isEqualTo("");
        assertThat(result.tags()).isEmpty();
        assertThat(result.entities()).isNotNull();
    }

    @Test
    void listReturnsPageMetadata() {
        File file = ownedFile();
        file.setName("a.pdf");
        when(fileRepository.search(eq(1L), isNull(), isNull(), isNull(), isNull(), isNull(), isNull(), isNull(),
                any(Pageable.class)))
                .thenReturn(new PageImpl<>(List.of(file), PageRequest.of(0, 20), 1));

        FileListResponse result = fileService.list(1L, null, null, null, null, null, null, null, 0);

        assertThat(result.items()).hasSize(1);
        assertThat(result.total()).isEqualTo(1);
        assertThat(result.page()).isEqualTo(0);
        assertThat(result.size()).isEqualTo(20);
    }

    @Test
    void moveFilesToFolderUpdatesAllFiles() {
        File first = ownedFile();
        File second = ownedFile();
        second.setId(2L);
        when(fileRepository.findByIdAndDeletedAtIsNull(1L)).thenReturn(Optional.of(first));
        when(fileRepository.findByIdAndDeletedAtIsNull(2L)).thenReturn(Optional.of(second));
        when(folderRepository.findByIdAndUsersId(5L, 1L)).thenReturn(Optional.of(new Folder()));

        fileService.moveFilesToFolder(1L, List.of(1L, 2L), 5L);

        assertThat(first.getFolderId()).isEqualTo(5L);
        assertThat(second.getFolderId()).isEqualTo(5L);
    }

    @Test
    void moveFilesToRootAcceptsNullFolder() {
        File file = ownedFile();
        file.setFolderId(9L);
        when(fileRepository.findByIdAndDeletedAtIsNull(1L)).thenReturn(Optional.of(file));

        fileService.moveFilesToFolder(1L, List.of(1L), null);

        assertThat(file.getFolderId()).isNull();
    }

    @Test
    void moveFilesRejectsBatchContainingAnotherUsersFile() {
        File mine = ownedFile();
        mine.setFolderId(9L);
        File theirs = ownedFile();
        theirs.setId(2L);
        theirs.setUsersId(2L);
        when(fileRepository.findByIdAndDeletedAtIsNull(1L)).thenReturn(Optional.of(mine));
        when(fileRepository.findByIdAndDeletedAtIsNull(2L)).thenReturn(Optional.of(theirs));

        assertThatThrownBy(() -> fileService.moveFilesToFolder(1L, List.of(1L, 2L), null))
                .isInstanceOf(ForbiddenException.class);

        assertThat(mine.getFolderId()).isEqualTo(9L);
    }

    @Test
    void moveFilesRejectsEmptyList() {
        assertThatThrownBy(() -> fileService.moveFilesToFolder(1L, List.of(), null))
                .isInstanceOf(BadRequestException.class);
    }

    @Test
    void restoreClearsDeletedAtAndReenqueuesIngest() {
        File file = ownedFile();
        file.setUploadsId(7L);
        file.setStatus(FileStatus.DELETED);
        file.setDeletedAt(LocalDateTime.now());
        when(fileRepository.findById(1L)).thenReturn(Optional.of(file));

        fileService.restore(1L, 1L);

        assertThat(file.getDeletedAt()).isNull();
        assertThat(file.getStatus()).isEqualTo(FileStatus.UPLOADED);
        verify(jobService).enqueueIngest(1L, 7L);
    }

    @Test
    void restoreDetachesFileFromDeletedFolder() {
        File file = ownedFile();
        file.setUploadsId(7L);
        file.setFolderId(5L);
        file.setDeletedAt(LocalDateTime.now());
        Folder folder = new Folder();
        folder.setDeletedAt(LocalDateTime.now());
        when(fileRepository.findById(1L)).thenReturn(Optional.of(file));
        when(folderRepository.findById(5L)).thenReturn(Optional.of(folder));

        fileService.restore(1L, 1L);

        assertThat(file.getFolderId()).isNull();
    }

    @Test
    void restoreDetachesFileWhenFolderNoLongerExists() {
        File file = ownedFile();
        file.setUploadsId(7L);
        file.setFolderId(5L);
        file.setDeletedAt(LocalDateTime.now());
        when(fileRepository.findById(1L)).thenReturn(Optional.of(file));
        when(folderRepository.findById(5L)).thenReturn(Optional.empty());

        fileService.restore(1L, 1L);

        assertThat(file.getFolderId()).isNull();
    }

    @Test
    void restoreKeepsFolderIdWhenFolderIsActive() {
        File file = ownedFile();
        file.setUploadsId(7L);
        file.setFolderId(5L);
        file.setDeletedAt(LocalDateTime.now());
        Folder folder = new Folder();
        when(fileRepository.findById(1L)).thenReturn(Optional.of(file));
        when(folderRepository.findById(5L)).thenReturn(Optional.of(folder));

        fileService.restore(1L, 1L);

        assertThat(file.getFolderId()).isEqualTo(5L);
    }

    @Test
    void restoreRejectsFileOwnedByAnotherUser() {
        File file = ownedFile();
        file.setUsersId(2L);
        file.setDeletedAt(LocalDateTime.now());
        when(fileRepository.findById(1L)).thenReturn(Optional.of(file));

        assertThatThrownBy(() -> fileService.restore(1L, 1L))
                .isInstanceOf(ForbiddenException.class);
    }

    @Test
    void restoreRejectsFileThatIsNotDeleted() {
        File file = ownedFile();
        when(fileRepository.findById(1L)).thenReturn(Optional.of(file));

        assertThatThrownBy(() -> fileService.restore(1L, 1L))
                .isInstanceOf(BadRequestException.class);
    }

    @Test
    void listTrashReturnsOnlyDeletedFiles() {
        File file = ownedFile();
        file.setName("gone.pdf");
        file.setDeletedAt(LocalDateTime.now());
        when(fileRepository.findByUsersIdAndDeletedAtIsNotNullOrderByDeletedAtDesc(eq(1L), any(Pageable.class)))
                .thenReturn(new PageImpl<>(List.of(file), PageRequest.of(0, 20), 1));

        FileListResponse result = fileService.listTrash(1L, 0);

        assertThat(result.items()).hasSize(1);
        assertThat(result.items().get(0).name()).isEqualTo("gone.pdf");
        assertThat(result.total()).isEqualTo(1);
    }

    @Test
    void getStorageUsageReturnsSumAndQuota() {
        when(fileRepository.sumSizeBytesByUsersId(1L)).thenReturn(2048L);

        StorageUsageResponse result = fileService.getStorageUsage(1L);

        assertThat(result.usedBytes()).isEqualTo(2048L);
        assertThat(result.quotaBytes()).isEqualTo(1000L);
    }

    @Test
    void permanentDeleteRemovesFileAndS3() {
        File file = ownedFile();
        file.setDeletedAt(LocalDateTime.now());
        file.setS3Key("files/key-1");
        when(fileRepository.findById(1L)).thenReturn(Optional.of(file));
        when(fileMetadataRepository.findById(1L)).thenReturn(Optional.empty());

        fileService.permanentDelete(1L, 1L);

        verify(s3Service).delete("test-bucket", "files/key-1");
        verify(jobRepository).deleteByFileId(1L);
        verify(fileRepository).delete(file);
    }

    @Test
    void permanentDeleteRejectsFileNotInTrash() {
        File file = ownedFile();
        when(fileRepository.findById(1L)).thenReturn(Optional.of(file));

        assertThatThrownBy(() -> fileService.permanentDelete(1L, 1L))
                .isInstanceOf(BadRequestException.class);
    }

    @Test
    void softDeleteByFolderIdsUsesGivenTimestampForActiveFiles() {
        File file = ownedFile();
        file.setFolderId(5L);
        LocalDateTime deletedAt = LocalDateTime.now();
        when(fileRepository.findByFolderIdInAndDeletedAtIsNull(List.of(5L))).thenReturn(List.of(file));

        fileService.softDeleteByFolderIds(List.of(5L), deletedAt);

        assertThat(file.getStatus()).isEqualTo(FileStatus.DELETED);
        assertThat(file.getDeletedAt()).isEqualTo(deletedAt);
    }

    @Test
    void restoreByFolderIdsOnlyRestoresFilesMatchingExactTimestamp() {
        File file = ownedFile();
        file.setFolderId(5L);
        file.setUploadsId(9L);
        LocalDateTime deletedAt = LocalDateTime.now();
        when(fileRepository.findByFolderIdInAndDeletedAt(List.of(5L), deletedAt)).thenReturn(List.of(file));

        fileService.restoreByFolderIds(List.of(5L), deletedAt);

        assertThat(file.getDeletedAt()).isNull();
        assertThat(file.getStatus()).isEqualTo(FileStatus.UPLOADED);
        verify(jobService).enqueueIngest(1L, 9L);
    }

    @Test
    void permanentDeleteByFolderIdsCleansUpS3AndJobRecords() {
        File file = ownedFile();
        file.setFolderId(5L);
        file.setS3Key("files/key-2");
        file.setDeletedAt(LocalDateTime.now());
        when(fileRepository.findByFolderIdInAndDeletedAtIsNotNull(List.of(5L))).thenReturn(List.of(file));
        when(fileMetadataRepository.findById(1L)).thenReturn(Optional.empty());

        fileService.permanentDeleteByFolderIds(List.of(5L));

        verify(s3Service).delete("test-bucket", "files/key-2");
        verify(jobRepository).deleteByFileId(1L);
        verify(fileRepository).deleteAll(List.of(file));
    }
}