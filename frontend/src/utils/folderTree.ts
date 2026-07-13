import type { Folder } from "../types";

/** folderId부터 루트까지 부모 체인을 따라가며 "이름/이름/..." 경로 문자열을 만든다. */
export function getFolderPath(folderId: number | null, folders: Folder[]): string {
  return getFolderAncestors(folderId, folders)
    .map((f) => f.name)
    .join("/");
}

/** folderId의 조상 목록을 루트→자신 순서로 반환한다(브레드크럼용). */
export function getFolderAncestors(folderId: number | null, folders: Folder[]): Folder[] {
  const chain: Folder[] = [];
  let current = folderId;
  while (current !== null) {
    const folder = folders.find((f) => f.folderId === current);
    if (!folder) break;
    chain.unshift(folder);
    current = folder.parentFolderId;
  }
  return chain;
}

/** folderId가 ancestorId 자신이거나 그 하위(자손)인지 확인한다. */
export function isDescendantOrSelf(folderId: number | null, ancestorId: number, folders: Folder[]): boolean {
  let current = folderId;
  while (current !== null) {
    if (current === ancestorId) return true;
    const folder = folders.find((f) => f.folderId === current);
    current = folder ? folder.parentFolderId : null;
  }
  return false;
}

/**
 * "기획/디자인/2026" 같은 슬래시 구분 경로를 받아 없는 중간 폴더를 생성하며 끝까지 내려간다.
 * 이미 존재하는 구간은 재사용한다. 새 폴더가 추가된 목록과 최종(leaf) folderId를 반환한다.
 */
export function ensureFolderPath(folders: Folder[], segments: string[]): { folders: Folder[]; leafId: number | null } {
  let current = [...folders];
  let parentId: number | null = null;
  let leafId: number | null = null;
  let nextId = current.reduce((max, f) => Math.max(max, f.folderId), 0) + 1;

  for (const rawName of segments) {
    const name = rawName.trim();
    if (!name) continue;

    const existing = current.find((f) => f.parentFolderId === parentId && f.name === name);
    if (existing) {
      parentId = existing.folderId;
      leafId = existing.folderId;
      continue;
    }

    const created: Folder = { folderId: nextId, name, parentFolderId: parentId };
    current = [...current, created];
    parentId = nextId;
    leafId = nextId;
    nextId += 1;
  }

  return { folders: current, leafId };
}
