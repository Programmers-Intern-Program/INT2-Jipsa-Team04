package com.jipsa.organize;

import java.util.List;

/** AI가 제안한 폴더 구조 재편 — 새로 만들 폴더 목록 + 파일별 이동/이름변경 매핑. */
public record OrganizeProposal(List<ProposedFolder> newFolders, List<FileMapping> mappings) {
}
