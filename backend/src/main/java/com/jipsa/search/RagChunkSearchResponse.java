package com.jipsa.search;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

@JsonIgnoreProperties(ignoreUnknown = true)
public record RagChunkSearchResponse(
        @JsonProperty("user_idx") Long userIdx,
        @JsonProperty("result_count") Integer resultCount,
        @JsonProperty("results") List<Result> results
) {
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Result(
            @JsonProperty("chunk_id") String chunkId,
            @JsonProperty("score") Double score,
            @JsonProperty("rag_document_idx") Long ragDocumentIdx,
            @JsonProperty("file_idx") Long fileIdx,
            @JsonProperty("folder_idx") Long folderIdx,
            @JsonProperty("file_name") String fileName,
            @JsonProperty("file_type") String fileType,
            @JsonProperty("chunk_index") Integer chunkIndex,
            @JsonProperty("content") String content,
            @JsonProperty("page") Integer page,
            @JsonProperty("section_title") String sectionTitle
    ) {
    }
}
