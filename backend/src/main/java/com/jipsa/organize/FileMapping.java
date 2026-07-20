package com.jipsa.organize;

/**
 * 파일 하나를 어디로 옮기고 어떤 이름으로 바꿀지에 대한 AI 제안.
 * targetFolderId(기존 폴더)와 targetTempId(이 제안에서 새로 만드는 폴더)는 동시에 채워질 수 없다.
 * 둘 다 비어 있으면 루트로 이동을 의미한다. newName이 없으면 이름은 그대로 둔다.
 *
 * @param fileId         이동/이름변경 대상 파일 id
 * @param targetFolderId 이동할 기존 폴더 id (없으면 null)
 * @param targetTempId   이동할, 이 제안에서 새로 만드는 폴더의 tempId (없으면 null)
 * @param newName        변경할 새 파일명 (변경 없으면 null)
 */
public record FileMapping(Long fileId, Long targetFolderId, String targetTempId, String newName) {
}
