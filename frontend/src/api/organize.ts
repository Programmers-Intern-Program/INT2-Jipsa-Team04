// GET/POST /api/v1/organize — backend/src/main/java/com/jipsa/organize 와 1:1 매칭.
// 스마트 정리(v0): 현재 폴더 트리 조회 → AI 제안 생성 → (사용자 승인 후) 제안 반영.
import type { OrganizeApplyResponse, OrganizeFolderTreeNode, OrganizeProposal } from "../types";
import { apiFetch } from "./client";

/** GET /api/v1/organize/current-tree — 미리보기의 "현재" 쪽에 쓸 본인 폴더 트리. */
export function getCurrentOrganizeTree(): Promise<OrganizeFolderTreeNode[]> {
  return apiFetch<{ folders: OrganizeFolderTreeNode[] }>("/organize/current-tree").then((res) => res.folders);
}

/** POST /api/v1/organize/propose — AI 제안 생성. 반환되는 OrganizeProposal은 이미 검증을 통과한 상태. */
export function proposeOrganization(): Promise<OrganizeProposal> {
  return apiFetch<OrganizeProposal>("/organize/propose", { method: "POST" });
}

/**
 * POST /api/v1/organize/apply — 제안을 검증 후 실제 파일 이동/이름변경 및 새 폴더 생성에 반영.
 * confidence가 사용자의 자동 분류 민감도보다 낮은 매핑은 반영되지 않고 응답의 held로 돌아오므로,
 * 호출부가 그 목록을 보고 사용자에게 알려줄 수 있도록 응답을 그대로 반환한다(과거엔 버렸음).
 */
export function applyOrganization(proposal: OrganizeProposal): Promise<OrganizeApplyResponse> {
  return apiFetch<OrganizeApplyResponse>("/organize/apply", {
    method: "POST",
    body: proposal,
  });
}
