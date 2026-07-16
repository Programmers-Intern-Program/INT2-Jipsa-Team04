package com.jipsa.organize;

import java.util.List;

/**
 * 현재 폴더 트리 + 파일 입력을 AI에 넘겨 폴더 구조 재편(OrganizeProposal)을 제안받는 클라이언트.
 *
 * 아직 구현체는 없다 — 실제 AI 연동(프롬프트 호출)은 후속 작업이며, 이 인터페이스는
 * OrganizeService가 AI 호출 방식에 의존하지 않고 "제안을 검증하고 반영하는" 로직부터
 * 먼저 완성해두기 위한 자리표시(placeholder)다. 연동 시 이 인터페이스의 구현체를 만들어
 * Bean으로 등록하면 된다.
 */
public interface AiOrganizeClient {

    OrganizeProposal proposeOrganization(List<FolderTreeNode> currentTree, List<OrganizeFileInput> files);
}
