package com.jipsa.folder;

/** GET /api/v1/folders 목록 항목. */
public record FolderResponse(Long folderId, String name, Long parentFolderId) {

    static FolderResponse from(Folder folder) {
        return new FolderResponse(folder.getId(), folder.getName(), folder.getParentFolderId());
    }
}
