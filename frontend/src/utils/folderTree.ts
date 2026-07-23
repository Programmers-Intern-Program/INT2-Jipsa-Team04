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

export interface FolderMatch {
  folderId: number;
  path: string;
  score: number;
  matchedKeywords: string[];
}

/**
 * 문서의 이름/태그/유형/요약/프로젝트명과 폴더(및 조상 경로) 이름의 키워드 겹침을 기준으로
 * "새 폴더를 만들지 않고" 지금 있는 폴더 중 가장 적합한 곳을 찾는다.
 * 일치하는 폴더가 하나도 없으면 null을 반환한다(= 현재 위치 유지 권장).
 */
export function findBestMatchingFolder(
  doc: { name: string; tags: string[]; docType?: string; summary: string; entities?: { project: string } },
  folders: Folder[]
): FolderMatch | null {
  const haystack = [doc.name, doc.docType ?? "", doc.summary, doc.entities?.project ?? "", ...doc.tags]
    .join(" ")
    .toLowerCase();

  let best: FolderMatch | null = null;

  for (const folder of folders) {
    const ancestors = getFolderAncestors(folder.folderId, folders);
    let score = 0;
    const matchedKeywords: string[] = [];

    ancestors.forEach((ancestor, idx) => {
      const name = ancestor.name.toLowerCase();
      if (name.length >= 2 && haystack.includes(name)) {
        const isLeaf = idx === ancestors.length - 1;
        score += isLeaf ? 2 : 1; // 말단 폴더 이름과의 직접 일치에 가중치
        matchedKeywords.push(ancestor.name);
      }
    });

    if (score > 0 && (!best || score > best.score)) {
      best = {
        folderId: folder.folderId,
        path: ancestors.map((a) => a.name).join("/"),
        score,
        matchedKeywords
      };
    }
  }

  return best;
}
