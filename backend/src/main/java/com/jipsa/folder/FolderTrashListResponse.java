package com.jipsa.folder;

import java.util.List;

/** GET /api/v1/folders/trash 응답: {folders:[...], total, page, size}. */
public record FolderTrashListResponse(
        List<FolderResponse> folders,
        long total,
        int page,
        int size
) {
}
