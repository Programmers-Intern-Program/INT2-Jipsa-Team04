package com.jipsa.organize;

import java.util.List;
import java.util.Set;

/**
 * 현재 폴더 트리 + 파일 입력을 AI에 넘겨 폴더 구조 재편(OrganizeProposal)을 제안받는 클라이언트.
 *
 * 구현체는 {@link AnthropicOrganizeClient}(Claude 호출) 하나뿐이다. OrganizeService가 AI
 * 호출 방식(현재는 Anthropic)에 직접 의존하지 않도록 인터페이스 뒤로
 * 분리해뒀다.
 */
public interface AiOrganizeClient {

    OrganizeProposal proposeOrganization(List<FolderTreeNode> currentTree, List<OrganizeFileInput> files);

    /**
     * 방금 업로드된 파일(targetFileIds)만 이동/이름변경 대상으로 삼는 스코프 제안.
     * targetFileIds에 없는 파일은 기존 명명 규칙·폴더 구조를 파악하기 위한 컨텍스트로만 쓰고
     * 옮기거나 이름을 바꾸지 않는다. allowRename이 false면 newName을 제안하지 않는다.
     */
    OrganizeProposal proposeForNewFiles(List<FolderTreeNode> currentTree,
                                        List<OrganizeFileInput> files,
                                        Set<Long> targetFileIds,
                                        boolean allowRename);
}
