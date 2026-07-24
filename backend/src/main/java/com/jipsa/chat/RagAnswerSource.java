package com.jipsa.chat;

import com.fasterxml.jackson.annotation.JsonProperty;

public record RagAnswerSource(
        @JsonProperty("source_id") String sourceId,
        @JsonProperty("chunk_id") String chunkId,
        @JsonProperty("rag_document_idx") Long ragDocumentIdx,
        @JsonProperty("file_idx") Long fileIdx,
        @JsonProperty("folder_idx") Long folderIdx,
        @JsonProperty("file_name") String fileName,
        @JsonProperty("file_type") String fileType,
        @JsonProperty("chunk_index") Integer chunkIndex,
        @JsonProperty("score") Double score,
        @JsonProperty("page") Integer page,
        @JsonProperty("slide_no") Integer slideNo,
        @JsonProperty("sheet_name") String sheetName,
        @JsonProperty("section_title") String sectionTitle,
        @JsonProperty("excerpt") String excerpt
) {
}