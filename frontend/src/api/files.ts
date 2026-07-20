import type { Document } from "../types";
import { apiFetch } from "./client";

export interface FileListItem {
    fileId: number;
    name: string;
    fileType: string;
    sizeBytes: number;
    folderId: number | null;
    status: string;
    star: boolean;
    modifiedAt: string;
}

export interface FileListResponse {
    items: FileListItem[];
    total: number;
    page: number;
    size: number;
}

export interface FileListParams {
    folderId?: number | null;
    keyword?: string;
    docType?: string;
    tags?: string;
    dateFrom?: string;
    dateTo?: string;
    page?: number;
}

export function listFiles(params: FileListParams = {}): Promise<FileListResponse> {
    const query = new URLSearchParams();
    if (params.folderId != null) query.set("folderId", String(params.folderId));
    if (params.keyword) query.set("keyword", params.keyword);
    if (params.docType) query.set("docType", params.docType);
    if (params.tags) query.set("tags", params.tags);
    if (params.dateFrom) query.set("dateFrom", params.dateFrom);
    if (params.dateTo) query.set("dateTo", params.dateTo);
    if (params.page != null) query.set("page", String(params.page));
    const qs = query.toString();
    return apiFetch<FileListResponse>(`/files${qs ? `?${qs}` : ""}`);
}

/**
 * /api/v1/files 전체 페이지를 순회해 로그인 사용자의 전체 문서를 Document[]로 모아 돌려준다.
 * 스마트 정리 적용처럼 폴더/파일이 서버에서 바뀐 뒤 App.tsx의 documents 상태 전체를
 * 새로 채워야 할 때 사용 — listFiles()는 한 페이지(PAGE_SIZE=20)만 주기 때문에 그대로 쓰면
 * 21번째 문서부터 documents 상태에서 사라지는 문제가 생긴다.
 */
export async function listAllFiles(): Promise<Document[]> {
    const first = await listFiles({ page: 0 });
    const items = [...first.items];
    const totalPages = first.size > 0 ? Math.ceil(first.total / first.size) : 1;
    for (let page = 1; page < totalPages; page++) {
        const res = await listFiles({ page });
        items.push(...res.items);
    }
    return items.map(toDocument);
}

export function toDocument(item: FileListItem): Document {
    return {
        id: String(item.fileId),
        name: item.name,
        content: "",
        sizeBytes: item.sizeBytes,
        fileType: item.fileType,
        folderId: item.folderId,
        tags: [],
        modifiedAt: item.modifiedAt,
        ownerName: "",
        securityRank: "일반",
        summary: "",
        piiDetected: false,
        status: item.status,
        star: item.star,
    };
}

export function moveFiles(fileIds: number[], folderId: number | null): Promise<void> {
    return apiFetch<{ success: boolean }>("/files/batch/move", {
        method: "PATCH",
        body: { fileIds, folderId },
    }).then(() => undefined);
}

export function listTrash(page = 0): Promise<FileListResponse> {
    return apiFetch<FileListResponse>(`/files/trash?page=${page}`);
}

export function restoreFile(fileId: number): Promise<void> {
    return apiFetch<{ success: boolean }>(`/files/${fileId}/restore`, {
        method: "PATCH",
    }).then(() => undefined);
}

export interface StorageUsage {
    usedBytes: number;
    quotaBytes: number;
}

export function getStorageUsage(): Promise<StorageUsage> {
    return apiFetch<StorageUsage>("/files/storage");
}