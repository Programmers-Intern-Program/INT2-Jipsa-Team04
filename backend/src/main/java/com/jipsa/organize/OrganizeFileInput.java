package com.jipsa.organize;

import java.time.LocalDateTime;

/**
 * 스마트 정리 AI 프롬프트에 넘길 파일 입력 v0.
 * File 엔티티의 기존 필드(파일명, 확장자, 크기, 소속 폴더, 업로드일)만 사용하고
 * 요약/태그 등 메타데이터는 포함하지 않는다. 메타데이터 파이프라인이 준비되면
 * {@link OrganizeInputAssembler}의 다른 구현체로 교체한다.
 */
public record OrganizeFileInput(
        Long fileId,
        String name,
        String extension,
        Long sizeBytes,
        Long folderId,
        LocalDateTime uploadedAt) {
}
