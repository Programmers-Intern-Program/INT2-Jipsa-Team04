package com.jipsa.folder;

import java.util.List;

/** GET /api/v1/folders 응답 전체: {folders:[...]}. */
public record FolderListResponse(List<FolderResponse> folders) {
}
