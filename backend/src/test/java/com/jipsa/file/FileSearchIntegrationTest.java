package com.jipsa.file;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.data.jpa.test.autoconfigure.DataJpaTest;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.LocalTime;

import static org.assertj.core.api.Assertions.assertThat;

@DataJpaTest
class FileSearchIntegrationTest {

    @Autowired
    private FileRepository fileRepository;
    @Autowired
    private FileMetadataRepository fileMetadataRepository;

    private File file(Long userId, String name, String type) {
        File f = new File();
        f.setUsersId(userId);
        f.setName(name);
        f.setS3Key("files/" + name + "-" + userId + "-" + System.nanoTime());
        f.setFileType(type);
        f.setSizeBytes(100L);
        return fileRepository.saveAndFlush(f);
    }

    private void metadata(Long fileId, String tagsJson) {
        FileMetadata m = new FileMetadata();
        m.setFileId(fileId);
        m.setFileType("pdf");
        m.setTags(tagsJson);
        fileMetadataRepository.saveAndFlush(m);
    }

    @Test
    void filtersByOwnership() {
        File mine = file(1L, "mine.pdf", "pdf");
        file(2L, "theirs.pdf", "pdf");

        Page<File> result = fileRepository.search(1L, null, null, null, null, null, null, null,
                PageRequest.of(0, 20));

        assertThat(result.getContent()).extracting(File::getId).containsExactly(mine.getId());
    }

    @Test
    void filtersByTag() {
        File tagged = file(1L, "taxdoc.pdf", "pdf");
        metadata(tagged.getId(), "[\"tax\",\"2026\"]");
        File other = file(1L, "memo.pdf", "pdf");
        metadata(other.getId(), "[\"memo\"]");

        Page<File> hit = fileRepository.search(1L, null, null, null, "tax", null, null, null,
                PageRequest.of(0, 20));
        Page<File> miss = fileRepository.search(1L, null, null, null, "unknown", null, null, null,
                PageRequest.of(0, 20));

        assertThat(hit.getContent()).extracting(File::getId).containsExactly(tagged.getId());
        assertThat(miss.getContent()).isEmpty();
    }

    @Test
    void filtersByDateRange() {
        File today = file(1L, "today.pdf", "pdf");
        LocalDateTime startOfToday = LocalDate.now().atStartOfDay();
        LocalDateTime endOfToday = LocalDate.now().atTime(LocalTime.MAX);
        LocalDateTime startTomorrow = LocalDate.now().plusDays(1).atStartOfDay();

        Page<File> included = fileRepository.search(1L, null, null, null, null, startOfToday, endOfToday, null,
                PageRequest.of(0, 20));
        Page<File> excluded = fileRepository.search(1L, null, null, null, null, startTomorrow, null, null,
                PageRequest.of(0, 20));

        assertThat(included.getContent()).extracting(File::getId).contains(today.getId());
        assertThat(excluded.getContent()).isEmpty();
    }

    @Test
    void storageSumExcludesDeletedAndOtherUsers() {
        File a = file(1L, "a.pdf", "pdf");
        a.setSizeBytes(100L);
        File b = file(1L, "b.pdf", "pdf");
        b.setSizeBytes(50L);
        File deleted = file(1L, "gone.pdf", "pdf");
        deleted.setSizeBytes(999L);
        deleted.setDeletedAt(LocalDateTime.now());
        File other = file(2L, "other.pdf", "pdf");
        other.setSizeBytes(777L);
        fileRepository.flush();

        assertThat(fileRepository.sumSizeBytesByUsersId(1L)).isEqualTo(150L);
    }
}