package com.jipsa.organize;

import java.util.List;

/**
 * 현재 폴더 트리 + 파일 입력을 AI에 넘겨 폴더 구조 재편(OrganizeProposal)을 제안받는 클라이언트.
 *
 * 구현체는 {@link AnthropicOrganizeClient}(Claude 호출) 하나뿐이다. OrganizeService가 AI
 * 호출 방식(현재는 Anthropic)에 직접 의존하지 않도록 인터페이스 뒤로
 * 분리해뒀다.
 */
public interface AiOrganizeClient {

    OrganizeProposal proposeOrganization(List<FolderTreeNode> currentTree, List<OrganizeFileInput> files);
}
