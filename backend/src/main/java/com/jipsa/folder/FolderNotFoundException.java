package com.jipsa.folder;

import com.jipsa.common.NotFoundException;

/** 폴더가 없거나(존재하지 않음) 요청한 사용자 소유가 아닌 경우. 두 경우를 구분해서 노출하지 않는다(정보 노출 방지). */
public class FolderNotFoundException extends NotFoundException {
    public FolderNotFoundException(Long folderId) {
        super("폴더를 찾을 수 없습니다: " + folderId);
    }
}
