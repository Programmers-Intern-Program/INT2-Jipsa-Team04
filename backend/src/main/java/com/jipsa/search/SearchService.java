package com.jipsa.search;

import com.jipsa.common.BadRequestException;
import com.jipsa.file.File;
import com.jipsa.file.FileRepository;
import com.jipsa.file.FileStatus;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Service
public class SearchService {

    private static final int MAX_SCOPE_FILES = 20;
    private static final int DEFAULT_TOP_K = 20;
    private static final int MAX_QUERY_LENGTH = 4096;
    private static final int MATCHED_CHUNK_MAX_LENGTH = 300;

    private final FileRepository fileRepository;
    private final RagChunkSearchClient ragChunkSearchClient;

    public SearchService(FileRepository fileRepository, RagChunkSearchClient ragChunkSearchClient) {
        this.fileRepository = fileRepository;
        this.ragChunkSearchClient = ragChunkSearchClient;
    }

    public SearchResponse search(Long userId, SearchRequest request) {
        String query = normalizeQuery(request == null ? null : request.query());
        Long folderId = request == null ? null : request.folderId();

        List<Long> scopeFileIds = resolveScope(userId, folderId);
        if (scopeFileIds.isEmpty()) {
            return new SearchResponse(List.of());
        }

        RagChunkSearchResponse rag = ragChunkSearchClient.search(
                new RagChunkSearchRequest(userId, query, DEFAULT_TOP_K, null, scopeFileIds));

        return new SearchResponse(toItems(rag));
    }

    private String normalizeQuery(String query) {
        if (query == null || query.isBlank()) {
            throw new BadRequestException("검색어를 입력해 주세요.");
        }
        String trimmed = query.trim();
        if (trimmed.length() > MAX_QUERY_LENGTH) {
            throw new BadRequestException("검색어가 너무 깁니다.");
        }
        return trimmed;
    }

    private List<Long> resolveScope(Long userId, Long folderId) {
        Pageable limit = PageRequest.of(0, MAX_SCOPE_FILES);
        List<File> files = folderId == null
                ? fileRepository.findByUsersIdAndStatusAndDeletedAtIsNullOrderByCreatedAtDesc(
                        userId, FileStatus.READY, limit)
                : fileRepository.findByUsersIdAndFolderIdAndStatusAndDeletedAtIsNullOrderByCreatedAtDesc(
                        userId, folderId, FileStatus.READY, limit);
        List<Long> ids = new ArrayList<>(files.size());
        for (File file : files) {
            ids.add(file.getId());
        }
        return ids;
    }

    private List<SearchResponse.Item> toItems(RagChunkSearchResponse rag) {
        if (rag == null || rag.results() == null) {
            return List.of();
        }
        Map<Long, SearchResponse.Item> bestPerFile = new LinkedHashMap<>();
        for (RagChunkSearchResponse.Result result : rag.results()) {
            if (result.fileIdx() == null) {
                continue;
            }
            double score = result.score() == null ? 0.0 : result.score();
            SearchResponse.Item existing = bestPerFile.get(result.fileIdx());
            if (existing == null || score > existing.score()) {
                bestPerFile.put(result.fileIdx(), new SearchResponse.Item(
                        result.fileIdx(),
                        result.fileName(),
                        truncate(result.content()),
                        score));
            }
        }
        return new ArrayList<>(bestPerFile.values());
    }

    private String truncate(String content) {
        if (content == null) {
            return null;
        }
        if (content.length() <= MATCHED_CHUNK_MAX_LENGTH) {
            return content;
        }
        return content.substring(0, MATCHED_CHUNK_MAX_LENGTH) + "…";
    }
}
