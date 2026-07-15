package com.jipsa.file;

import com.jipsa.common.BadRequestException;
import com.jipsa.common.exception.FileNotFoundException;
import com.jipsa.common.exception.ForbiddenException;
import com.jipsa.folder.Folder;
import com.jipsa.folder.FolderNotFoundException;
import com.jipsa.folder.FolderRepository;
import com.jipsa.job.JobRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.core.io.ByteArrayResource;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.when;

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

    private FileService fileService;

    @BeforeEach
    void setUp() {
        fileService = new FileService(fileRepository, jobRepository, folderRepository,
                s3Service, "test-bucket");
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
}