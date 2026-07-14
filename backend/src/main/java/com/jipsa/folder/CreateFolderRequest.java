package com.jipsa.folder;

import jakarta.validation.constraints.NotBlank;

/** POST /api/v1/folders 요청. parentFolderId 미지정(null) 시 루트에 생성. */
public record CreateFolderRequest(@NotBlank String name, Long parentFolderId) {
}
