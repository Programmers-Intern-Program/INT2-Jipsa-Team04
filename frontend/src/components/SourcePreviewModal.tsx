import { useState } from "react";
import { X, Download, FileText, MapPin, Sparkles, Gauge, SearchX } from "lucide-react";
import type { Citation } from "../api/chat";
import { downloadFile } from "../api/files";
import FilePreview, { type HighlightStatus } from "./FilePreview";

interface SourcePreviewModalProps {
    citation: Citation;
    fileName: string;
    fileType: string;
    onClose: () => void;
}

export default function SourcePreviewModal({ citation, fileName, fileType, onClose }: SourcePreviewModalProps) {
    const [highlightStatus, setHighlightStatus] = useState<HighlightStatus | "checking">("checking");
    const locationLabel =
        citation.page != null ? `${citation.page}페이지` : citation.sectionTitle ? citation.sectionTitle : "위치 정보 없음";
    const typeLabel = fileType ? fileType.toUpperCase() : "FILE";

    return (
        <div
            className="fixed inset-0 z-[120] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
            onClick={onClose}
        >
            <div
                className="bg-white rounded-2xl w-full max-w-6xl h-[90vh] flex flex-col overflow-hidden shadow-2xl"
                onClick={(e) => e.stopPropagation()}
            >
                <div className="flex items-center justify-between gap-4 px-5 py-4 border-b border-outline-variant">
                    <div className="flex min-w-0 items-center gap-3">
                        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-secondary/10 text-secondary">
                            <FileText className="h-5 w-5" />
                        </div>
                        <div className="min-w-0">
                            <h3 className="truncate text-base font-bold text-on-surface">{fileName}</h3>
                            <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] font-semibold text-outline">
                                <span className="rounded-md bg-surface-container px-2 py-0.5">{typeLabel}</span>
                                <span className="flex items-center gap-1"><MapPin className="h-3 w-3" />{locationLabel}</span>
                                {citation.sectionTitle && citation.page != null && <span className="truncate">{citation.sectionTitle}</span>}
                                {citation.score != null && (
                                    <span className="flex items-center gap-1">
                                        <Gauge className="h-3 w-3" />
                                        관련도 {(citation.score * 100).toFixed(0)}%
                                    </span>
                                )}
                            </div>
                        </div>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                        <button
                            type="button"
                            onClick={() => downloadFile(citation.fileId, fileName)}
                            className="p-2 text-outline hover:text-primary hover:bg-surface-container rounded-lg cursor-pointer"
                            title="원본 다운로드"
                        >
                            <Download className="w-5 h-5" />
                        </button>
                        <button
                            type="button"
                            onClick={onClose}
                            className="p-2 text-outline hover:text-on-surface hover:bg-surface-container rounded-lg cursor-pointer"
                            title="닫기"
                        >
                            <X className="w-5 h-5" />
                        </button>
                    </div>
                </div>
                <div className="grid min-h-0 flex-1 grid-rows-[auto_minmax(0,1fr)] bg-surface-container-lowest lg:grid-cols-[minmax(260px,0.36fr)_minmax(0,1fr)] lg:grid-rows-1">
                    <aside className="border-b border-outline-variant bg-white p-5 lg:overflow-y-auto lg:border-b-0 lg:border-r">
                        <div className="flex items-center gap-2 text-secondary">
                            <Sparkles className="h-4 w-4" />
                            <h4 className="text-sm font-bold">AI가 사용한 근거</h4>
                        </div>
                        {citation.excerpt ? (
                            <div className="mt-3 rounded-xl border border-secondary/20 bg-secondary/[0.04] p-4 text-sm leading-7 text-on-surface">
                                {citation.excerpt}
                            </div>
                        ) : (
                            <div className="mt-3 rounded-xl border border-outline-variant bg-surface-container-low p-4 text-sm text-on-surface-variant">
                                이 인용에는 별도의 근거 문장이 포함되지 않았습니다.
                            </div>
                        )}
                        {highlightStatus === "checking" ? (
                            <p className="mt-3 flex items-start gap-2 rounded-lg bg-surface-container-low px-3 py-2 text-xs font-semibold leading-5 text-on-surface-variant">
                                <span className="mt-1 h-3 w-3 shrink-0 animate-spin rounded-full border-2 border-secondary border-t-transparent" />
                                문서에서 근거 위치를 찾고 있습니다.
                            </p>
                        ) : highlightStatus === "found" ? (
                            <p className="mt-3 flex items-start gap-2 rounded-lg bg-emerald-50 px-3 py-2 text-xs font-semibold leading-5 text-emerald-700">
                                <MapPin className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                                문서에서 근거 위치를 찾아 강조했습니다.
                            </p>
                        ) : (
                            <p className="mt-3 flex items-start gap-2 rounded-lg bg-amber-50 px-3 py-2 text-xs font-semibold leading-5 text-amber-800">
                                <SearchX className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                                문서 내 정확한 위치를 찾지 못했습니다.
                            </p>
                        )}
                    </aside>
                    <div className="min-h-0 overflow-hidden">
                        <FilePreview
                            key={`${citation.fileId}-${citation.page ?? "none"}-${citation.sectionTitle ?? ""}-${citation.excerpt ?? ""}`}
                            fileId={citation.fileId}
                            fileName={fileName}
                            fileType={fileType}
                            highlight={{ excerpt: citation.excerpt, sectionTitle: citation.sectionTitle, page: citation.page }}
                            onHighlightStatusChange={setHighlightStatus}
                            className="h-full"
                        />
                    </div>
                </div>
            </div>
        </div>
    );
}
