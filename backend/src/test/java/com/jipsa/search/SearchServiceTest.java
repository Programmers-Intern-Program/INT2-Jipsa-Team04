package com.jipsa.search;

import com.jipsa.common.BadRequestException;
import com.jipsa.file.File;
import com.jipsa.file.FileRepository;
import com.jipsa.file.FileStatus;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.data.domain.Pageable;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class SearchServiceTest {

    private static final double SCORE_THRESHOLD = 0.3;

    @Mock private FileRepository fileRepository;
    @Mock private RagChunkSearchClient ragChunkSearchClient;

    private SearchService searchService;

    @BeforeEach
    void setUp() {
        searchService = new SearchService(fileRepository, ragChunkSearchClient, SCORE_THRESHOLD);
    }

    private File readyFile(Long id, String name) {
        File file = new File();
        file.setId(id);
        file.setName(name);
        file.setStatus(FileStatus.READY);
        return file;
    }

    private RagChunkSearchResponse.Result result(Long fileIdx, String name, String content, Double score) {
        return new RagChunkSearchResponse.Result(
                "11111111-1111-1111-1111-11111111111" + fileIdx, score, 100L, fileIdx, null,
                name, "pdf", 0, content, 1, null);
    }

    @Test
    void blankQueryIsRejectedWithoutCallingRag() {
        assertThatThrownBy(() -> searchService.search(7L, new SearchRequest("   ", null)))
                .isInstanceOf(BadRequestException.class);

        verifyNoInteractions(ragChunkSearchClient);
    }

    @Test
    void noReadyFilesReturnsEmptyWithoutCallingRag() {
        when(fileRepository.findByUsersIdAndStatusAndDeletedAtIsNullOrderByCreatedAtDesc(
                eq(7L), eq(FileStatus.READY), any(Pageable.class)))
                .thenReturn(List.of());

        SearchResponse response = searchService.search(7L, new SearchRequest("배포 절차", null));

        assertThat(response.items()).isEmpty();
        verify(ragChunkSearchClient, never()).search(any());
    }

    @Test
    void forwardsResolvedIdsAndConfiguredThresholdAndCollapsesToBestChunkPerFile() {
        when(fileRepository.findByUsersIdAndStatusAndDeletedAtIsNullOrderByCreatedAtDesc(
                eq(7L), eq(FileStatus.READY), any(Pageable.class)))
                .thenReturn(List.of(readyFile(11L, "파일11.pdf"), readyFile(22L, "파일22.pdf")));

        RagChunkSearchResponse ragResponse = new RagChunkSearchResponse(7L, 3, List.of(
                result(11L, "파일11.pdf", "낮은 점수 청크", 0.40),
                result(11L, "파일11.pdf", "높은 점수 청크", 0.90),
                result(22L, "파일22.pdf", "다른 파일 청크", 0.70)));
        when(ragChunkSearchClient.search(any())).thenReturn(ragResponse);

        ArgumentCaptor<RagChunkSearchRequest> captor = ArgumentCaptor.forClass(RagChunkSearchRequest.class);

        SearchResponse response = searchService.search(7L, new SearchRequest("배포 절차", null));

        verify(ragChunkSearchClient).search(captor.capture());
        assertThat(captor.getValue().referenceFileIdxs()).containsExactly(11L, 22L);
        assertThat(captor.getValue().userIdx()).isEqualTo(7L);
        assertThat(captor.getValue().scoreThreshold()).isEqualTo(SCORE_THRESHOLD);

        assertThat(response.items()).hasSize(2);
        SearchResponse.Item file11 = response.items().stream()
                .filter(item -> item.fileId().equals(11L)).findFirst().orElseThrow();
        assertThat(file11.matchedChunk()).isEqualTo("높은 점수 청크");
        assertThat(file11.score()).isEqualTo(0.90);
    }

    @Test
    void usesCurrentDbNameInsteadOfStaleRagFileName() {
        when(fileRepository.findByUsersIdAndStatusAndDeletedAtIsNullOrderByCreatedAtDesc(
                eq(7L), eq(FileStatus.READY), any(Pageable.class)))
                .thenReturn(List.of(readyFile(11L, "새이름.pdf")));
        when(ragChunkSearchClient.search(any()))
                .thenReturn(new RagChunkSearchResponse(7L, 1, List.of(
                        result(11L, "옛이름.pdf", "본문 청크", 0.80))));

        SearchResponse response = searchService.search(7L, new SearchRequest("배포 절차", null));

        assertThat(response.items()).hasSize(1);
        assertThat(response.items().get(0).name()).isEqualTo("새이름.pdf");
    }

    @Test
    void dropsResultsOutsideResolvedScope() {
        when(fileRepository.findByUsersIdAndStatusAndDeletedAtIsNullOrderByCreatedAtDesc(
                eq(7L), eq(FileStatus.READY), any(Pageable.class)))
                .thenReturn(List.of(readyFile(11L, "파일11.pdf")));
        when(ragChunkSearchClient.search(any()))
                .thenReturn(new RagChunkSearchResponse(7L, 2, List.of(
                        result(11L, "파일11.pdf", "범위 안 청크", 0.80),
                        result(99L, "남의파일.pdf", "범위 밖 청크", 0.95))));

        SearchResponse response = searchService.search(7L, new SearchRequest("배포 절차", null));

        assertThat(response.items()).hasSize(1);
        assertThat(response.items().get(0).fileId()).isEqualTo(11L);
    }

    @Test
    void folderScopeUsesFolderFinder() {
        when(fileRepository.findByUsersIdAndFolderIdAndStatusAndDeletedAtIsNullOrderByCreatedAtDesc(
                eq(7L), eq(9L), eq(FileStatus.READY), any(Pageable.class)))
                .thenReturn(List.of(readyFile(11L, "파일11.pdf")));
        when(ragChunkSearchClient.search(any()))
                .thenReturn(new RagChunkSearchResponse(7L, 1, List.of(
                        result(11L, "파일11.pdf", "폴더 범위 청크", 0.55))));

        SearchResponse response = searchService.search(7L, new SearchRequest("배포 절차", 9L));

        assertThat(response.items()).hasSize(1);
        verify(fileRepository, never())
                .findByUsersIdAndStatusAndDeletedAtIsNullOrderByCreatedAtDesc(any(), any(), any());
    }
}
