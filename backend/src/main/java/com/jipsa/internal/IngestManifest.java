package com.jipsa.internal;

import com.fasterxml.jackson.annotation.JsonProperty;

public record IngestManifest(
        @JsonProperty("file_idx") Long fileIdx,
        @JsonProperty("user_idx") Long userIdx,
        @JsonProperty("folder_idx") Long folderIdx,
        @JsonProperty("file_name") String fileName,
        @JsonProperty("file_type") String fileType,
        @JsonProperty("download_url") String downloadUrl,
        @JsonProperty("url_expires_in") long urlExpiresIn
) {
}