package com.jipsa.organize;

/**
 * 파일 하나를 어디로 옮기고 어떤 이름으로 바꿀지에 대한 AI 제안.
 * targetFolderId(기존 폴더)와 targetTempId(이 제안에서 새로 만드는 폴더)는 동시에 채워질 수 없다.
 * 둘 다 비어 있으면 루트로 이동을 의미한다. newName이 없으면 이름은 그대로 둔다.
 *
 * confidence: 이 매핑이 적절하다는 확신도(0~1). apply 시점에 사용자의 자동 분류 민감도
 * 설정과 비교해서, 민감도보다 낮은 매핑은 자동 반영에서 제외되고 보류된다(OrganizeService 참고).
 * null이면(AI가 값을 주지 않았거나, 이 필드가 없던 시절 호출부와의 호환) 확신도 비교 없이
 * 그대로 반영한다 — 4-인자 생성자는 이 경우를 위한 것이다.
 *
 * @param fileId         이동/이름변경 대상 파일 id
 * @param targetFolderId 이동할 기존 폴더 id (없으면 null)
 * @param targetTempId   이동할, 이 제안에서 새로 만드는 폴더의 tempId (없으면 null)
 * @param newName        변경할 새 파일명 (변경 없으면 null)
 * @param confidence     이 매핑에 대한 확신도, 0.0~1.0 (없으면 null)
 */
public record FileMapping(Long fileId, Long targetFolderId, String targetTempId, String newName, Double confidence) {

    public FileMapping(Long fileId, Long targetFolderId, String targetTempId, String newName) {
        this(fileId, targetFolderId, targetTempId, newName, null);
    }
}
