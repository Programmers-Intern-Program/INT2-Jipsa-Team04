package com.jipsa.organize;

/**
 * AI가 제안한, 아직 실제로 존재하지 않는 새 폴더.
 * tempId는 이 제안 안에서만 유효한 임시 식별자로, FileMapping.targetTempId나
 * 다른 ProposedFolder.parentTempId가 이 폴더를 부모로 참조할 때 사용한다.
 * parentTempId와 parentFolderId는 동시에 채워질 수 없다(정확히 하나만 사용, 실제 검증은
 * {@link OrganizeService}에서 수행).
 *
 * @param tempId         이 제안 안에서의 임시 식별자(고유해야 함)
 * @param name           새로 만들 폴더 이름
 * @param parentTempId   부모가 "이 제안 안의 다른 새 폴더"인 경우 그 폴더의 tempId
 * @param parentFolderId 부모가 "기존에 존재하는 폴더"인 경우 그 폴더의 id
 */
public record ProposedFolder(String tempId, String name, String parentTempId, Long parentFolderId) {
}
