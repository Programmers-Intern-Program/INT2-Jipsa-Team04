import {
    createContext,
    useCallback,
    useContext,
    useEffect,
    useRef,
    useState,
} from "react";
import type { ReactNode } from "react";
import { getRecentUploads, uploadOne } from "../api/uploads";

export type UploadItemStatus =
    | "QUEUED"
    | "UPLOADING"
    | "UPLOADED"
    | "PROCESSING"
    | "READY"
    | "FAILED"
    | "INVALID";

export interface UploadItem {
    id: string;
    name: string;
    size: number;
    file?: File;
    folderId: number | null;
    fileId?: number;
    status: UploadItemStatus;
    error?: string;
    progress?: number;
}

const ALLOWED_EXTS = ["pdf", "txt"];
const MAX_BYTES = 20 * 1024 * 1024;

function validate(file: File): string | null {
    const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
    if (!ALLOWED_EXTS.includes(ext)) return "지원하지 않는 형식";
    if (file.size > MAX_BYTES) return "20MB 초과";
    return null;
}

function mapServerStatus(status: string): UploadItemStatus {
    if (status === "PROCESSING") return "PROCESSING";
    if (status === "READY") return "READY";
    if (status === "FAILED") return "FAILED";
    return "UPLOADED";
}

interface UploadContextValue {
    items: UploadItem[];
    isBusy: boolean;
    uploadedSignal: number;
    enqueue: (files: File[], folderId: number | null) => void;
    startAll: () => void;
    removeItem: (id: string) => void;
    retryItem: (id: string) => void;
    clearSettled: () => void;
    refreshRecent: () => void;
}

const UploadContext = createContext<UploadContextValue | null>(null);

export function UploadProvider({ children }: { children: ReactNode }) {
    const [items, setItems] = useState<UploadItem[]>([]);
    const [uploadedSignal, setUploadedSignal] = useState(0);
    const itemsRef = useRef<UploadItem[]>([]);
    const runningRef = useRef(false);

    const commit = useCallback((next: UploadItem[]) => {
        itemsRef.current = next;
        setItems(next);
    }, []);

    const patch = useCallback(
        (id: string, p: Partial<UploadItem>) => {
            commit(itemsRef.current.map((it) => (it.id === id ? { ...it, ...p } : it)));
        },
        [commit]
    );

    const pump = useCallback(async () => {
        if (runningRef.current) return;
        runningRef.current = true;
        try {
            const CONCURRENCY = 5;
            const inFlight = new Set<Promise<void>>();
            const hasQueued = () => itemsRef.current.some((it) => it.status === "QUEUED");
            const startNext = () => {
                const target = itemsRef.current.find((it) => it.status === "QUEUED");
                if (!target || !target.file) return;
                const id = target.id;
                const file = target.file;
                const folderId = target.folderId;
                patch(id, { status: "UPLOADING", progress: 0, error: undefined });
                const task = (async () => {
                    try {
                        const result = await uploadOne(file, folderId, (loaded, total) => {
                            patch(id, { progress: total > 0 ? Math.round((loaded / total) * 100) : 0 });
                        });
                        patch(id, { status: "UPLOADED", progress: 100, fileId: result.fileIds[0] });
                        setUploadedSignal((n) => n + 1);
                    } catch (e) {
                        patch(id, { status: "FAILED", error: e instanceof Error ? e.message : "업로드 실패" });
                    }
                })();
                inFlight.add(task);
                task.finally(() => inFlight.delete(task));
            };

            while (hasQueued() || inFlight.size > 0) {
                while (inFlight.size < CONCURRENCY && hasQueued()) {
                    startNext();
                }
                if (inFlight.size === 0) break;
                await Promise.race(inFlight);
            }
        } finally {
            runningRef.current = false;
        }
    }, [patch]);

    const enqueue = useCallback(
        (files: File[], folderId: number | null) => {
            if (files.length === 0) return;
            const added: UploadItem[] = files.map((file) => {
                const error = validate(file);
                return {
                    id: crypto.randomUUID(),
                    name: file.name,
                    size: file.size,
                    file,
                    folderId,
                    status: error ? "INVALID" : "QUEUED",
                    error: error ?? undefined,
                };
            });
            commit([...itemsRef.current, ...added]);
        },
        [commit]
    );

    const startAll = useCallback(() => {
        void pump();
    }, [pump]);

    const removeItem = useCallback(
        (id: string) => {
            commit(itemsRef.current.filter((it) => it.id !== id));
        },
        [commit]
    );

    const retryItem = useCallback(
        (id: string) => {
            patch(id, { status: "QUEUED", error: undefined });
            void pump();
        },
        [patch, pump]
    );

    const clearSettled = useCallback(() => {
        commit(
            itemsRef.current.filter(
                (it) => it.status === "QUEUED" || it.status === "UPLOADING"
            )
        );
    }, [commit]);

    const refreshRecent = useCallback(async () => {
        const rows = await getRecentUploads(20).catch(() => null);
        if (!rows) return;
        const known = new Set(
            itemsRef.current
                .map((it) => it.fileId)
                .filter((v): v is number => v != null)
        );
        const updated = itemsRef.current.map((it) => {
            if (it.fileId == null) return it;
            const row = rows.find((r) => r.fileId === it.fileId);
            if (!row) return it;
            return { ...it, status: mapServerStatus(row.status), error: row.errorMessage ?? undefined };
        });
        const fresh: UploadItem[] = rows
            .filter((r) => r.status !== "DELETED" && !known.has(r.fileId))
            .map((r) => ({
                id: `srv-${r.fileId}`,
                name: r.name,
                size: r.sizeBytes,
                folderId: null,
                fileId: r.fileId,
                status: mapServerStatus(r.status),
                error: r.errorMessage ?? undefined,
                progress: 100,
            }));
        commit([...updated, ...fresh]);
    }, [commit]);

    useEffect(() => {
        void refreshRecent();
    }, [refreshRecent]);

    const isBusy = items.some(
        (it) => it.status === "QUEUED" || it.status === "UPLOADING"
    );

    return (
        <UploadContext.Provider
            value={{
                items,
                isBusy,
                uploadedSignal,
                enqueue,
                startAll,
                removeItem,
                retryItem,
                clearSettled,
                refreshRecent,
            }}
        >
            {children}
        </UploadContext.Provider>
    );
}

export function useUploads(): UploadContextValue {
    const ctx = useContext(UploadContext);
    if (!ctx) throw new Error("useUploads must be used within UploadProvider");
    return ctx;
}