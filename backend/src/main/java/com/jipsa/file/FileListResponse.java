package com.jipsa.file;

import java.util.List;

public record FileListResponse(
        List<FileListItem> items,
        long total
) {
}