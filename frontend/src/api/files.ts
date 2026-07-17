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