package com.jipsa.file;

import java.util.List;

public record MoveFilesRequest(
        List<Long> fileIds,
        Long folderId
) {
}