// GET/POST/PATCH/DELETE /api/v1/folders — backend/src/main/java/com/jipsa/folder 와 1:1 매칭.
import type { Folder } from "../types";
import { apiFetch } from "./client";

/** GET /api/v1/folders — 본인 소유 폴더 전체 평면 목록. */
export function listFolders(): Promise<Folder[]> {
  return apiFetch<{ folders: Folder[] }>("/folders").then((res) => res.folders);
}

/** POST /api/v1/folders — parentFolderId 생략/undefined면 루트에 생성. */
export function createFolder(name: string, parentFolderId?: number | null): Promise<number> {
  return apiFetch<{ folderId: number }>("/folders", {
    method: "POST",
    body: { name, parentFolderId: parentFolderId ?? undefined },
  }).then((res) => res.folderId);
}

/**
 * PATCH /api/v1/folders/{id} — 이름 변경 및/또는 부모 이동.
 * patch에 없는 키는 "변경 안 함", parentFolderId: null은 "루트로 이동"이라는 의미다
 * (JSON.stringify가 undefined 키는 생략하고 null은 그대로 보내주는 걸 그대로 활용).
 */
export function updateFolder(
  id: number,
  patch: { name?: string; parentFolderId?: number | null }
): Promise<void> {
  return apiFetch<{ success: boolean }>(`/folders/${id}`, {
    method: "PATCH",
    body: patch,
  }).then(() => undefined);
}

/**
 * DELETE /api/v1/folders/{id} — 소프트 삭제(휴지통 이동). 하위 폴더·파일도 함께 휴지통으로 간다.
 * 예전 하드 삭제 시절과 달리, 호출 전에 안의 파일을 다른 폴더로 옮겨둘 필요가 없다
 * (파일도 서버가 알아서 휴지통으로 보낸다) — 오히려 미리 옮겨버리면 그 파일들은
 * 이 폴더의 삭제 대상에서 빠져서 휴지통이 아니라 그냥 활성 상태로 [미분류]에 남는다.
 */
export function deleteFolder(id: number): Promise<void> {
  return apiFetch<{ success: boolean }>(`/folders/${id}`, { method: "DELETE" }).then(() => undefined);
}

export interface FolderTrashListResponse {
  folders: Folder[];
  total: number;
  page: number;
  size: number;
}

/** GET /api/v1/folders/trash — 휴지통에 있는 폴더 목록(페이지네이션). */
export function listFolderTrash(page = 0): Promise<FolderTrashListResponse> {
  return apiFetch<FolderTrashListResponse>(`/folders/trash?page=${page}`);
}

/** /api/v1/folders/trash 전체 페이지를 순회해 휴지통 폴더 전체를 모아 돌려준다. */
export async function listAllFolderTrash(): Promise<Folder[]> {
  const first = await listFolderTrash(0);
  const items = [...first.folders];
  const totalPages = first.size > 0 ? Math.ceil(first.total / first.size) : 1;
  for (let page = 1; page < totalPages; page++) {
    const res = await listFolderTrash(page);
    items.push(...res.folders);
  }
  return items;
}

/** PATCH /api/v1/folders/{id}/restore — 폴더 복원(하위 폴더·파일도 함께 복원). */
export function restoreFolder(id: number): Promise<void> {
  return apiFetch<{ success: boolean }>(`/folders/${id}/restore`, { method: "PATCH" }).then(() => undefined);
}

/** DELETE /api/v1/folders/{id}/permanent — 폴더 영구 삭제(하위 파일 S3 실물까지 정리). */
export function permanentDeleteFolder(id: number): Promise<void> {
  return apiFetch<{ success: boolean }>(`/folders/${id}/permanent`, { method: "DELETE" }).then(() => undefined);
}
