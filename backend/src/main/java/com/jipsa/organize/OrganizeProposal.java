package com.jipsa.organize;

import java.util.List;

/**
 * AI가 제안한 폴더 구조 재편 — 새로 만들 폴더 목록 + 파일별 이동/이름변경 매핑.
 *
 * idempotencyKey: /organize/apply 요청 바디에서만 쓰인다(propose 응답엔 항상 null).
 * 클라이언트가 같은 승인 동작에 대해 매번 같은 키를 실어 보내면, OrganizeService가 짧은 시간
 * 안의 재요청(네트워크 응답 유실 후 재시도 등)을 중복 반영하지 않고 걸러낸다.
 * 2-인자 생성자는 기존 호출부(테스트 포함) 호환용 — idempotencyKey 없이 쓰면 매번 새로 반영된다.
 */
public record OrganizeProposal(List<ProposedFolder> newFolders, List<FileMapping> mappings, String idempotencyKey) {

    public OrganizeProposal(List<ProposedFolder> newFolders, List<FileMapping> mappings) {
        this(newFolders, mappings, null);
    }
}
