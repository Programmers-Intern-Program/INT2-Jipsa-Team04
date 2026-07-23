import type { Document } from "../types";
import { ApiError, apiFetch } from "./client";

const TOKEN_STORAGE_KEY = "aidrive_token";

export interface FileListItem {
    fileId: number;
    name: string;
    fileType: string;
    sizeBytes: number;
    folderId: number | null;
    status: string;
    star: boolean;
    modifiedAt: string;
    summary: string;
    tags: string[];
    securityRank: string | null;
    documentType: string | null;
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
    documentType?: string;
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
    if (params.documentType) query.set("documentType", params.documentType);
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
        tags: item.tags ?? [],
        modifiedAt: item.modifiedAt,
        ownerName: "",
        securityRank: item.securityRank === "기밀" ? "기밀" : "일반",
        summary: item.summary ?? "",
        piiDetected: false,
        status: item.status,
        star: item.star,
        documentType: item.documentType ?? null,
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

export async function listAllTrash(): Promise<Document[]> {
    const first = await listTrash(0);
    const items = [...first.items];
    const totalPages = first.size > 0 ? Math.ceil(first.total / first.size) : 1;
    for (let page = 1; page < totalPages; page++) {
        const res = await listTrash(page);
        items.push(...res.items);
    }
    return items.map(toDocument);
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

export interface FileDetail {
    name: string;
    fileType: string;
    sizeBytes: number;
    folderId: number | null;
    ownerName: string;
    star: boolean;
    summary: string;
    tags: string[];
    entities: {
        dates: string[];
        people: string[];
        amounts: string[];
        project: string | null;
    };
    modifiedAt: string;
    status: string;
    processingStage: string | null;
    securityRank: "일반" | "기밀";
    piiDetected: boolean;
    documentType: string | null;
    extractionStatus: string | null;
    extractionConfidence: number | null;
}

export function getFileDetail(fileId: number): Promise<FileDetail> {
    return apiFetch<FileDetail>(`/files/${fileId}`);
}

export function toggleStar(fileId: number, star: boolean): Promise<void> {
    return apiFetch<{ success: boolean }>(`/files/${fileId}/star`, {
        method: "PATCH",
        body: { star },
    }).then(() => undefined);
}

export function renameFile(fileId: number, name: string): Promise<void> {
    return apiFetch<{ success: boolean }>(`/files/${fileId}/name`, {
        method: "PATCH",
        body: { name },
    }).then(() => undefined);
}

export function moveFile(fileId: number, folderId: number | null): Promise<void> {
    return apiFetch<{ success: boolean }>(`/files/${fileId}`, {
        method: "PATCH",
        body: { folderId },
    }).then(() => undefined);
}

export function setFileTags(fileId: number, tags: string[]): Promise<void> {
    return apiFetch<{ success: boolean }>(`/files/${fileId}/tags`, {
        method: "PATCH",
        body: { tags },
    }).then(() => undefined);
}

export function setFileDocumentType(fileId: number, documentType: string | null): Promise<void> {
    return apiFetch<{ success: boolean }>(`/files/${fileId}/document-type`, {
        method: "PATCH",
        body: { documentType },
    }).then(() => undefined);
}

export function getDocumentTypes(): Promise<string[]> {
    return apiFetch<{ documentTypes: string[] }>("/metadata/document-types").then((res) => res.documentTypes);
}

async function fetchBlob(path: string): Promise<Blob> {
    const token = localStorage.getItem(TOKEN_STORAGE_KEY);
    const response = await fetch(`/api/v1${path}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!response.ok) throw new ApiError(response.status, response.statusText);
    return response.blob();
}

export async function downloadFile(fileId: number, name: string): Promise<void> {
    const blob = await fetchBlob(`/files/${fileId}/download`);
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
}

export async function viewFile(fileId: number): Promise<void> {
    const blob = await fetchBlob(`/files/${fileId}/view`);
    const url = URL.createObjectURL(blob);
    window.open(url, "_blank");
    setTimeout(() => URL.revokeObjectURL(url), 60000);
}

export interface FileStatusInfo {
    status: string;
    processingStage: string | null;
    attempts: number;
    errorMessage: string | null;
}

export function getFileStatus(fileId: number): Promise<FileStatusInfo> {
    return apiFetch<FileStatusInfo>(`/files/${fileId}/status`);
}

export function deleteFile(fileId: number): Promise<void> {
    return apiFetch<{ success: boolean }>(`/files/${fileId}`, {
        method: "DELETE",
    }).then(() => undefined);
}

export function permanentDeleteFile(fileId: number): Promise<void> {
    return apiFetch<{ success: boolean }>(`/files/${fileId}/permanent`, {
        method: "DELETE",
    }).then(() => undefined);
}