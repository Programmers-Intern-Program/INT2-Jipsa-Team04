package com.jipsa.search;

import com.jipsa.common.BadRequestException;
import com.jipsa.file.File;
import com.jipsa.file.FileRepository;
import com.jipsa.file.FileStatus;
import org.springframework.beans.factory.annotation.Value;
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
    private final Double scoreThreshold;

    public SearchService(FileRepository fileRepository,
                         RagChunkSearchClient ragChunkSearchClient,
                         @Value("${app.rag.search-score-threshold:0.3}") Double scoreThreshold) {
        this.fileRepository = fileRepository;
        this.ragChunkSearchClient = ragChunkSearchClient;
        this.scoreThreshold = scoreThreshold;
    }

    public SearchResponse search(Long userId, SearchRequest request) {
        String query = normalizeQuery(request == null ? null : request.query());
        Long folderId = request == null ? null : request.folderId();

        List<File> scopeFiles = resolveScopeFiles(userId, folderId);
        if (scopeFiles.isEmpty()) {
            return new SearchResponse(List.of());
        }

        Map<Long, String> currentNameById = new LinkedHashMap<>();
        for (File file : scopeFiles) {
            currentNameById.put(file.getId(), file.getName());
        }
        List<Long> scopeFileIds = new ArrayList<>(currentNameById.keySet());

        RagChunkSearchResponse rag = ragChunkSearchClient.search(
                new RagChunkSearchRequest(userId, query, DEFAULT_TOP_K, scoreThreshold, scopeFileIds));

        return new SearchResponse(toItems(rag, currentNameById));
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

    private List<File> resolveScopeFiles(Long userId, Long folderId) {
        Pageable limit = PageRequest.of(0, MAX_SCOPE_FILES);
        return folderId == null
                ? fileRepository.findByUsersIdAndStatusAndDeletedAtIsNullOrderByCreatedAtDesc(
                        userId, FileStatus.READY, limit)
                : fileRepository.findByUsersIdAndFolderIdAndStatusAndDeletedAtIsNullOrderByCreatedAtDesc(
                        userId, folderId, FileStatus.READY, limit);
    }

    private List<SearchResponse.Item> toItems(RagChunkSearchResponse rag, Map<Long, String> currentNameById) {
        if (rag == null || rag.results() == null) {
            return List.of();
        }
        Map<Long, SearchResponse.Item> bestPerFile = new LinkedHashMap<>();
        for (RagChunkSearchResponse.Result result : rag.results()) {
            Long fileIdx = result.fileIdx();
            String currentName = fileIdx == null ? null : currentNameById.get(fileIdx);
            if (currentName == null) {
                continue;
            }
            double score = result.score() == null ? 0.0 : result.score();
            SearchResponse.Item existing = bestPerFile.get(fileIdx);
            if (existing == null || score > existing.score()) {
                bestPerFile.put(fileIdx, new SearchResponse.Item(
                        fileIdx,
                        currentName,
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
