package com.jipsa.folder;

import com.jipsa.common.BadRequestException;

/** parentFolderId로 자기 자신 또는 자신의 자손 폴더를 지정하려는 경우. */
public class FolderCircularReferenceException extends BadRequestException {
    public FolderCircularReferenceException(Long folderId, Long parentFolderId) {
        super("폴더(%d)를 자기 자신 또는 하위 폴더(%d)로 이동할 수 없습니다".formatted(folderId, parentFolderId));
    }
}
