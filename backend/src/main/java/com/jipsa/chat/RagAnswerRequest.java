package com.jipsa.chat;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

@JsonInclude(JsonInclude.Include.NON_NULL)
public record RagAnswerRequest(
        @JsonProperty("user_idx") Long userIdx,
        @JsonProperty("query") String query,
        @JsonProperty("top_k") Integer topK,
        @JsonProperty("score_threshold") Double scoreThreshold,
        @JsonProperty("reference_file_idxs") List<Long> referenceFileIdxs
) {
}