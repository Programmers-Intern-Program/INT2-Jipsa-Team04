import { X, Download, FileText, MapPin, Sparkles } from "lucide-react";
import type { Citation } from "../api/chat";
import { downloadFile } from "../api/files";
import FilePreview from "./FilePreview";

interface SourcePreviewModalProps {
    citation: Citation;
    fileName: string;
    fileType: string;
    documentOnly?: boolean;
    onClose: () => void;
}

export default function SourcePreviewModal({
    citation,
    fileName,
    fileType,
    documentOnly = false,
    onClose,
}: SourcePreviewModalProps) {
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
                                {!documentOnly && (
                                    <span className="flex items-center gap-1"><MapPin className="h-3 w-3" />{locationLabel}</span>
                                )}
                                {!documentOnly && citation.sectionTitle && citation.page != null && (
                                    <span className="truncate">{citation.sectionTitle}</span>
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
                            <h4 className="text-sm font-bold">{documentOnly ? "참조 문서" : "AI가 사용한 근거"}</h4>
                        </div>
                        {documentOnly ? (
                            <div className="mt-3 rounded-xl border border-outline-variant bg-surface-container-low p-4 text-sm leading-6 text-on-surface-variant">
                                AI 답변에 사용된 문서의 전체 내용을 확인할 수 있습니다.
                            </div>
                        ) : citation.excerpt ? (
                            <div className="mt-3 rounded-xl border border-secondary/20 bg-secondary/[0.04] p-4 text-sm leading-7 text-on-surface">
                                {citation.excerpt}
                            </div>
                        ) : (
                            <div className="mt-3 rounded-xl border border-outline-variant bg-surface-container-low p-4 text-sm text-on-surface-variant">
                                이 인용에는 별도의 근거 문장이 포함되지 않았습니다.
                            </div>
                        )}
                    </aside>
                    <div className="min-h-0 overflow-hidden">
                        <FilePreview
                            key={`${citation.fileId}-${documentOnly ? "document" : `${citation.page ?? "none"}-${citation.sectionTitle ?? ""}-${citation.excerpt ?? ""}`}`}
                            fileId={citation.fileId}
                            fileName={fileName}
                            fileType={fileType}
                            location={documentOnly ? undefined : {
                                excerpt: citation.excerpt,
                                sectionTitle: citation.sectionTitle,
                                page: citation.page,
                            }}
                            className="h-full"
                        />
                    </div>
                </div>
            </div>
        </div>
    );
}
