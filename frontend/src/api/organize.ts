// GET/POST /api/v1/organize — backend/src/main/java/com/jipsa/organize 와 1:1 매칭.
// 스마트 정리(v0): 현재 폴더 트리 조회 → AI 제안 생성 → (사용자 승인 후) 제안 반영.
import type { OrganizeApplyResponse, OrganizeFolderTreeNode, OrganizeProposal } from "../types";
import { apiFetch } from "./client";

/** GET /api/v1/organize/current-tree — 미리보기의 "현재" 쪽에 쓸 본인 폴더 트리. */
export function getCurrentOrganizeTree(): Promise<OrganizeFolderTreeNode[]> {
  return apiFetch<{ folders: OrganizeFolderTreeNode[] }>("/organize/current-tree").then((res) => res.folders);
}

/** POST /api/v1/organize/propose — AI 제안 생성. 반환되는 OrganizeProposal은 이미 검증을 통과한 상태. */
export function proposeOrganization(allowRename: boolean): Promise<OrganizeProposal> {
  return apiFetch<OrganizeProposal>(`/organize/propose?allowRename=${allowRename}`, { method: "POST" });
}

/**
 * POST /api/v1/organize/propose-for-upload — 방금 업로드된 파일(fileIds)만 이동/이름변경 대상으로
 * 삼는 스코프 제안. 나머지 파일은 컨텍스트로만 쓰인다. 업로드 완료 후 한 번만 호출한다.
 */
export function proposeForUpload(fileIds: number[], allowRename: boolean): Promise<OrganizeProposal> {
  return apiFetch<OrganizeProposal>(`/organize/propose-for-upload?allowRename=${allowRename}`, {
    method: "POST",
    body: { fileIds },
  });
}

export function applyOrganization(proposal: OrganizeProposal): Promise<OrganizeApplyResponse> {
  return apiFetch<OrganizeApplyResponse>("/organize/apply", {
    method: "POST",
    body: proposal,
  });
}
