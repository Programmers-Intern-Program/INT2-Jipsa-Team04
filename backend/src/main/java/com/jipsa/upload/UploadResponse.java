package com.jipsa.upload;

import java.util.List;

public record UploadResponse(Long uploadId, List<Long> fileIds) {
}