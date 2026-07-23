import { useEffect, useState } from "react";
import { X, FileText, Tag, ShieldAlert, Calendar, Users, Coins, FolderClosed } from "lucide-react";
import type { Folder as FolderType } from "../types";
import { formatBytes } from "../utils/formatBytes";
import { getFolderPath } from "../utils/folderTree";
import { getFileDetail, setFileTags, setFileDocumentType, getDocumentTypes, type FileDetail } from "../api/files";

interface FileDetailPanelProps {
    fileId: number | null;
    folders: FolderType[];
    onClose: () => void;
    onTagsChanged?: (fileId: number, tags: string[]) => void;
    onDocumentTypeChanged?: (fileId: number, documentType: string | null) => void;
}

export default function FileDetailPanel({ fileId, folders, onClose, onTagsChanged, onDocumentTypeChanged }: FileDetailPanelProps) {
    const [detail, setDetail] = useState<FileDetail | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(false);
    const [tags, setTags] = useState<string[]>([]);
    const [newTag, setNewTag] = useState("");
    const [savingTags, setSavingTags] = useState(false);
    const [documentType, setDocumentType] = useState<string | null>(null);
    const [documentTypeOptions, setDocumentTypeOptions] = useState<string[]>([]);
    const [savingDocType, setSavingDocType] = useState(false);

    useEffect(() => {
        let cancelled = false;
        getDocumentTypes()
            .then((types) => { if (!cancelled) setDocumentTypeOptions(types); })
            .catch(() => {});
        return () => { cancelled = true; };
    }, []);

    useEffect(() => {
        if (fileId == null) {
            setDetail(null);
            return;
        }
        let cancelled = false;
        setLoading(true);
        setError(false);
        getFileDetail(fileId)
            .then((d) => { if (!cancelled) { setDetail(d); setTags(d.tags ?? []); setDocumentType(d.documentType ?? null); } })
            .catch(() => { if (!cancelled) setError(true); })
            .finally(() => { if (!cancelled) setLoading(false); });
        return () => { cancelled = true; };
    }, [fileId]);

    const persistTags = async (next: string[]) => {
        if (fileId == null) return;
        const prev = tags;
        setTags(next);
        setSavingTags(true);
        try {
            await setFileTags(fileId, next);
            onTagsChanged?.(fileId, next);
        } catch (err) {
            console.warn("[files] PATCH /api/v1/files/{id}/tags 실패 - 롤백:", err);
            setTags(prev);
            alert("태그 저장에 실패했습니다.");
        } finally {
            setSavingTags(false);
        }
    };

    const addTag = () => {
        const value = newTag.trim();
        if (!value || tags.includes(value)) { setNewTag(""); return; }
        persistTags([...tags, value]);
        setNewTag("");
    };

    const removeTag = (tag: string) => {
        persistTags(tags.filter((t) => t !== tag));
    };

    const persistDocumentType = async (next: string | null) => {
        if (fileId == null) return;
        const prev = documentType;
        setDocumentType(next);
        setSavingDocType(true);
        try {
            await setFileDocumentType(fileId, next);
            onDocumentTypeChanged?.(fileId, next);
        } catch (err) {
            console.warn("[files] PATCH /api/v1/files/{id}/document-type 실패 - 롤백:", err);
            setDocumentType(prev);
            alert("문서 종류 저장에 실패했습니다.");
        } finally {
            setSavingDocType(false);
        }
    };

    if (fileId == null) return null;

    const entities = detail?.entities;
    const hasEntities = !!entities && (
        entities.dates.length > 0 ||
        entities.people.length > 0 ||
        entities.amounts.length > 0 ||
        (entities.project != null && entities.project !== "")
    );
    const isProcessing = detail?.status === "PROCESSING" || detail?.status === "UPLOADED";

    return (
        <div className="fixed inset-0 z-[110] flex justify-end" id="file-detail-overlay">
            <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onClose}></div>
            <aside className="relative w-full max-w-md h-full bg-white shadow-2xl overflow-y-auto" id="file-detail-panel">
                <div className="sticky top-0 bg-white border-b border-outline-variant px-6 py-4 flex items-center justify-between z-10">
                    <h2 className="font-bold text-title-sm text-on-surface">문서 상세 정보</h2>
                    <button onClick={onClose} className="p-2 hover:bg-surface-container rounded-full text-outline hover:text-on-surface cursor-pointer" title="닫기">
                        <X className="w-5 h-5" />
                    </button>
                </div>

                {loading && <div className="p-8 text-center text-outline text-body-sm">불러오는 중...</div>}
                {error && !loading && (
                    <div className="p-8 text-center text-error text-body-sm">
                        상세 정보를 불러오지 못했습니다. (로그인 연동 전이면 정상)
                    </div>
                )}

                {detail && !loading && (
                    <div className="p-6 space-y-6">
                        <div className="flex items-start gap-3">
                            <div className="w-12 h-12 rounded-xl bg-primary/5 flex items-center justify-center shrink-0">
                                <FileText className="w-6 h-6 text-primary" />
                            </div>
                            <div className="min-w-0">
                                <p className="font-bold text-body-md text-on-surface break-words">{detail.name}</p>
                                <p className="text-[11px] text-outline mt-1">{detail.fileType?.toUpperCase()} · {formatBytes(detail.sizeBytes)}</p>
                            </div>
                        </div>

                        {isProcessing && (
                            <div className="flex items-center gap-2 bg-primary/5 text-primary rounded-xl px-3 py-2 text-body-sm font-semibold">
                                <span className="w-3.5 h-3.5 border-2 border-primary border-t-transparent rounded-full animate-spin"></span>
                                문서를 처리하는 중입니다. 요약·태그·추출 정보가 곧 채워집니다.
                            </div>
                        )}

                        <div className="grid grid-cols-2 gap-3 text-body-sm">
                            <div>
                                <p className="text-[11px] font-bold text-outline mb-1">상태</p>
                                <span className="inline-flex px-2 py-0.5 rounded-full text-[11px] font-bold bg-surface-container text-on-surface">{detail.status}</span>
                            </div>
                            <div>
                                <p className="text-[11px] font-bold text-outline mb-1">보안 등급</p>
                                <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold ${detail.securityRank === "기밀" ? "bg-rose-50 text-rose-600" : "bg-cyan-50 text-cyan-600"}`}>
                  {detail.securityRank === "기밀" && <ShieldAlert className="w-3 h-3" />}
                                    {detail.securityRank}
                </span>
                            </div>
                            <div>
                                <p className="text-[11px] font-bold text-outline mb-1">폴더</p>
                                <p className="flex items-center gap-1 text-on-surface"><FolderClosed className="w-3.5 h-3.5 text-outline" />{getFolderPath(detail.folderId, folders) || "미분류"}</p>
                            </div>
                            <div>
                                <p className="text-[11px] font-bold text-outline mb-1">소유자</p>
                                <p className="text-on-surface">{detail.ownerName || "-"}</p>
                            </div>
                            <div>
                                <p className="text-[11px] font-bold text-outline mb-1">수정일</p>
                                <p className="text-on-surface">{detail.modifiedAt?.slice(0, 10) || "-"}</p>
                            </div>
                        </div>

                        <div>
                            <p className="text-[11px] font-bold text-outline mb-1.5">문서 종류</p>
                            <select
                                value={documentType ?? ""}
                                onChange={(e) => persistDocumentType(e.target.value === "" ? null : e.target.value)}
                                disabled={savingDocType}
                                className="w-full bg-white border border-outline-variant rounded-lg px-2.5 py-2 text-body-sm outline-none focus:ring-1 focus:ring-primary transition-all disabled:opacity-50 cursor-pointer"
                            >
                                <option value="">미분류</option>
                                {documentTypeOptions.map((opt) => (
                                    <option key={opt} value={opt}>{opt}</option>
                                ))}
                            </select>
                        </div>

                        <div>
                            <p className="text-[11px] font-bold text-outline mb-1.5">AI 요약</p>
                            <p className="text-body-sm text-on-surface leading-relaxed bg-surface-container/50 rounded-xl p-3">
                                {detail.summary || "요약 정보가 아직 없습니다. (문서 처리 중이거나 미생성)"}
                            </p>
                        </div>

                        <div>
                            <p className="text-[11px] font-bold text-outline mb-1.5 flex items-center gap-1"><Tag className="w-3 h-3" /> 태그</p>
                            <div className="flex flex-wrap gap-1.5 items-center">
                                {tags.map((t) => (
                                    <span key={t} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-lg bg-primary/5 text-primary text-[11px] font-semibold">
                                        #{t}
                                        <button type="button" onClick={() => removeTag(t)} disabled={savingTags} className="hover:text-rose-500 disabled:opacity-40 cursor-pointer">
                                            <X className="w-3 h-3" />
                                        </button>
                                    </span>
                                ))}
                                {tags.length === 0 && <span className="text-body-sm text-outline">태그 없음</span>}
                            </div>
                            <div className="flex gap-1.5 mt-2">
                                <input
                                    type="text"
                                    value={newTag}
                                    onChange={(e) => setNewTag(e.target.value)}
                                    onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addTag(); } }}
                                    placeholder="태그 추가 후 Enter"
                                    disabled={savingTags}
                                    className="flex-1 bg-white border border-outline-variant rounded-lg px-2.5 py-1.5 text-[11px] outline-none focus:ring-1 focus:ring-primary transition-all font-semibold disabled:opacity-50"
                                />
                                <button type="button" onClick={addTag} disabled={savingTags || !newTag.trim()} className="px-3 bg-primary text-white text-[11px] font-bold rounded-lg hover:bg-opacity-95 cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed">
                                    추가
                                </button>
                            </div>
                        </div>

                        {hasEntities && entities && (
                            <div className="space-y-2">
                                <p className="text-[11px] font-bold text-outline mb-1.5">추출된 정보</p>
                                {entities.project && <p className="text-body-sm"><span className="font-bold text-outline">프로젝트:</span> {entities.project}</p>}
                                {entities.dates.length > 0 && <p className="text-body-sm flex items-start gap-1.5"><Calendar className="w-3.5 h-3.5 text-outline mt-0.5 shrink-0" /> {entities.dates.join(", ")}</p>}
                                {entities.people.length > 0 && <p className="text-body-sm flex items-start gap-1.5"><Users className="w-3.5 h-3.5 text-outline mt-0.5 shrink-0" /> {entities.people.join(", ")}</p>}
                                {entities.amounts.length > 0 && <p className="text-body-sm flex items-start gap-1.5"><Coins className="w-3.5 h-3.5 text-outline mt-0.5 shrink-0" /> {entities.amounts.join(", ")}</p>}
                            </div>
                        )}

                        {detail.extractionStatus && (
                            <div className="text-[11px] text-outline">
                                추출 상태: <span className="font-bold text-on-surface">{detail.extractionStatus}</span>
                                {detail.extractionConfidence != null && ` · 신뢰도 ${Math.round(detail.extractionConfidence * 100)}%`}
                            </div>
                        )}

                        {detail.piiDetected && (
                            <div className="flex items-center gap-2 bg-rose-50 text-rose-600 rounded-xl px-3 py-2 text-body-sm font-semibold">
                                <ShieldAlert className="w-4 h-4" /> 개인정보(PII)가 감지된 문서입니다.
                            </div>
                        )}
                    </div>
                )}
            </aside>
        </div>
    );
}