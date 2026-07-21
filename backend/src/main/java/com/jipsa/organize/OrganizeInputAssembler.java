package com.jipsa.organize;

import java.util.List;

/**
 * AI 폴더 구조 제안에 넘길 파일 입력을 조립하는 인터페이스.
 * v0 구현체는 {@link FileFieldOrganizeInputAssembler}(File 필드 기반)이고,
 * 요약/태그 파싱 파이프라인이 준비되면 메타데이터 기반 구현체로 교체할 수 있도록
 * 조립 로직을 이 인터페이스 뒤로 분리해둔다.
 */
public interface OrganizeInputAssembler {

    List<OrganizeFileInput> assemble(Long userId);
}
