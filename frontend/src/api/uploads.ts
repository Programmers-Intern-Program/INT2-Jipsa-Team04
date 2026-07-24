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
    folderId?: number | null,
    idempotencyKey?: string
): Promise<UploadResult> {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    if (folderId != null) form.append("folderId", String(folderId));

    const token = localStorage.getItem(TOKEN_STORAGE_KEY);
    const headers: Record<string, string> = {
        "Idempotency-Key": idempotencyKey ?? crypto.randomUUID(),
    };
    if (token) headers.Authorization = `Bearer ${token}`;
    const response = await fetch("/api/v1/uploads", {
        method: "POST",
        headers,
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

export function uploadOne(
    file: File,
    folderId: number | null,
    onProgress?: (loaded: number, total: number) => void
): Promise<UploadResult> {
    return new Promise((resolve, reject) => {
        const form = new FormData();
        form.append("files", file);
        if (folderId != null) form.append("folderId", String(folderId));

        const token = localStorage.getItem(TOKEN_STORAGE_KEY);
        const xhr = new XMLHttpRequest();
        xhr.open("POST", "/api/v1/uploads");
        xhr.setRequestHeader("Idempotency-Key", crypto.randomUUID());
        if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);

        xhr.upload.onprogress = (e) => {
            if (e.lengthComputable && onProgress) onProgress(e.loaded, e.total);
        };

        xhr.onload = () => {
            let payload: unknown = null;
            try {
                payload = JSON.parse(xhr.responseText);
            } catch {
                payload = null;
            }
            if (xhr.status >= 200 && xhr.status < 300) {
                resolve(payload as UploadResult);
            } else {
                const body = payload as { error?: { message?: string } | string } | null;
                const message =
                    (typeof body?.error === "object" ? body?.error?.message : (body?.error as string)) ??
                    xhr.statusText;
                reject(new ApiError(xhr.status, message));
            }
        };

        xhr.onerror = () => reject(new ApiError(0, "네트워크 오류"));
        xhr.send(form);
    });
}

export interface RecentUploadItem {
    fileId: number;
    name: string;
    fileType: string;
    sizeBytes: number;
    status: "UPLOADED" | "PROCESSING" | "READY" | "FAILED" | "DELETED";
    errorMessage: string | null;
    createdAt: string;
}

export function getRecentUploads(limit = 20): Promise<RecentUploadItem[]> {
    return apiFetch<RecentUploadItem[]>(`/uploads/recent?limit=${limit}`);
}