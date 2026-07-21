import { ApiError, apiFetch } from "./client";

const TOKEN_STORAGE_KEY = "aidrive_token";

export interface UploadResult {
    uploadId: number;
    fileIds: number[];
}

export type UploadStatusValue =
    | "PENDING"
    | "UPLOADING"
    | "COMPLETED"
    | "FAILED"
    | "CANCELLED";

export interface UploadStatusResponse {
    status: UploadStatusValue;
    total: number;
    createdAt: string;
    finishedAt: string | null;
}

export async function uploadFiles(
    files: File[],
    folderId?: number | null
): Promise<UploadResult> {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    if (folderId != null) form.append("folderId", String(folderId));

    const token = localStorage.getItem(TOKEN_STORAGE_KEY);
    const response = await fetch("/api/v1/uploads", {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: form,
    });

    if (!response.ok) {
        const message = await response
            .json()
            .then((d) => d?.error?.message ?? d?.error ?? response.statusText)
            .catch(() => response.statusText);
        throw new ApiError(response.status, message);
    }
    return response.json() as Promise<UploadResult>;
}

export function getUploadStatus(uploadId: number): Promise<UploadStatusResponse> {
    return apiFetch<UploadStatusResponse>(`/uploads/${uploadId}/status`);
}