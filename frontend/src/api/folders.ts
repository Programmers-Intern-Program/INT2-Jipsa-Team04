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

/** DELETE /api/v1/folders/{id} — 하위 폴더까지 재귀 삭제. */
export function deleteFolder(id: number): Promise<void> {
  return apiFetch<{ success: boolean }>(`/folders/${id}`, { method: "DELETE" }).then(() => undefined);
}
