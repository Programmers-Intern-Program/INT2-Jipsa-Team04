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
    sessionId: string;
    name: string;
    size: number;
    file?: File;
    folderId: number | null;
    fileId?: number;
    status: UploadItemStatus;
    error?: string;
    progress?: number;
    idempotencyKey: string;
}

export const DEFAULT_UPLOAD_SESSION_ID = "regular";

const ALLOWED_EXTS = ["pdf", "txt", "docx", "pptx", "xlsx"];
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
    isBusyForSession: (sessionId: string) => boolean;
    uploadedSignal: number;
    enqueue: (files: File[], folderId: number | null, sessionId?: string) => void;
    startAll: (sessionId?: string) => void;
    uploadQueuedAndWait: (sessionId?: string) => Promise<number[]>;
    removeItem: (id: string) => void;
    retryItem: (id: string) => void;
    clearSettled: (sessionId?: string) => void;
    clearSession: (sessionId: string) => void;
    clearPending: (sessionId?: string) => void;
    refreshRecent: (sessionId?: string) => void;
}

const UploadContext = createContext<UploadContextValue | null>(null);

export function UploadProvider({ children }: { children: ReactNode }) {
    const [items, setItems] = useState<UploadItem[]>([]);
    const [uploadedSignal, setUploadedSignal] = useState(0);
    const itemsRef = useRef<UploadItem[]>([]);
    const pumpPromisesRef = useRef(new Map<string, Promise<void>>());

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

    const pump = useCallback((sessionId: string = DEFAULT_UPLOAD_SESSION_ID) => {
        const existing = pumpPromisesRef.current.get(sessionId);
        if (existing) return existing;

        const task = (async () => {
            const CONCURRENCY = 5;
            const inFlight = new Set<Promise<void>>();
            const hasQueued = () => itemsRef.current.some((it) => it.sessionId === sessionId && it.status === "QUEUED");
            const startNext = () => {
                const target = itemsRef.current.find((it) => it.sessionId === sessionId && it.status === "QUEUED");
                if (!target) return;
                if (!target.file) {
                    patch(target.id, { status: "FAILED", error: "파일을 다시 선택해 주세요" });
                    return;
                }
                const id = target.id;
                const file = target.file;
                const folderId = target.folderId;
                const idempotencyKey = target.idempotencyKey;
                patch(id, { status: "UPLOADING", progress: 0, error: undefined });
                const task = (async () => {
                    try {
                        const result = await uploadOne(file, folderId, (loaded, total) => {
                            patch(id, { progress: total > 0 ? Math.round((loaded / total) * 100) : 0 });
                        }, idempotencyKey);
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
        })();
        pumpPromisesRef.current.set(sessionId, task);
        const clearPump = () => {
            if (pumpPromisesRef.current.get(sessionId) === task) {
                pumpPromisesRef.current.delete(sessionId);
            }
        };
        task.then(clearPump, clearPump);
        return task;
    }, [patch]);

    const enqueue = useCallback(
        (files: File[], folderId: number | null, sessionId: string = DEFAULT_UPLOAD_SESSION_ID) => {
            if (files.length === 0) return;
            const added: UploadItem[] = files.map((file) => {
                const error = validate(file);
                return {
                    id: crypto.randomUUID(),
                    sessionId,
                    name: file.name,
                    size: file.size,
                    file,
                    folderId,
                    status: error ? "INVALID" : "QUEUED",
                    error: error ?? undefined,
                    idempotencyKey: crypto.randomUUID(),
                };
            });
            commit([...itemsRef.current, ...added]);
        },
        [commit]
    );

    const startAll = useCallback((sessionId: string = DEFAULT_UPLOAD_SESSION_ID) => {
        void pump(sessionId);
    }, [pump]);

    const uploadQueuedAndWait = useCallback(async (sessionId: string = DEFAULT_UPLOAD_SESSION_ID) => {
        const runIds = itemsRef.current
            .filter((it) => it.sessionId === sessionId && it.status === "QUEUED")
            .map((it) => it.id);
        await pump(sessionId);
        return itemsRef.current
            .filter((it) => it.sessionId === sessionId && runIds.includes(it.id) && it.fileId != null)
            .map((it) => it.fileId as number);
    }, [pump]);

    const clearPending = useCallback((sessionId: string = DEFAULT_UPLOAD_SESSION_ID) => {
        commit(itemsRef.current.filter((it) => it.sessionId !== sessionId || (it.status !== "QUEUED" && it.status !== "INVALID")));
    }, [commit]);

    const clearSession = useCallback((sessionId: string) => {
        commit(itemsRef.current.filter((it) => it.sessionId !== sessionId));
    }, [commit]);

    const removeItem = useCallback(
        (id: string) => {
            commit(itemsRef.current.filter((it) => it.id !== id));
        },
        [commit]
    );

    const retryItem = useCallback(
        (id: string) => {
            const item = itemsRef.current.find((it) => it.id === id);
            if (!item) return;
            patch(id, { status: "QUEUED", error: undefined });
            void pump(item.sessionId);
        },
        [patch, pump]
    );

    const clearSettled = useCallback((sessionId: string = DEFAULT_UPLOAD_SESSION_ID) => {
        commit(
            itemsRef.current.filter(
                (it) => it.sessionId !== sessionId || it.status === "QUEUED" || it.status === "UPLOADING"
            )
        );
    }, [commit]);

    const refreshRecent = useCallback(async (sessionId: string = DEFAULT_UPLOAD_SESSION_ID) => {
        if (sessionId !== DEFAULT_UPLOAD_SESSION_ID) return;
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
                sessionId,
                name: r.name,
                size: r.sizeBytes,
                folderId: null,
                fileId: r.fileId,
                status: mapServerStatus(r.status),
                error: r.errorMessage ?? undefined,
                progress: 100,
                idempotencyKey: `srv-${r.fileId}`,
            }));
        commit([...updated, ...fresh]);
    }, [commit]);

    useEffect(() => {
        void refreshRecent(DEFAULT_UPLOAD_SESSION_ID);
    }, [refreshRecent]);

    const isBusyForSession = useCallback((sessionId: string) => items.some(
        (it) => it.sessionId === sessionId && (it.status === "QUEUED" || it.status === "UPLOADING")
    ), [items]);

    const isBusy = isBusyForSession(DEFAULT_UPLOAD_SESSION_ID);

    return (
        <UploadContext.Provider
            value={{
                items,
                isBusy,
                isBusyForSession,
                uploadedSignal,
                enqueue,
                startAll,
                uploadQueuedAndWait,
                removeItem,
                retryItem,
                clearSettled,
                clearSession,
                clearPending,
                refreshRecent,
            }}
        >
            {children}
        </UploadContext.Provider>
    );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useUploads(): UploadContextValue {
    const ctx = useContext(UploadContext);
    if (!ctx) throw new Error("useUploads must be used within UploadProvider");
    return ctx;
}
