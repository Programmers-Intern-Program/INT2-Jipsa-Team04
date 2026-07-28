import { X, Download, FileText } from "lucide-react";
import type { Citation } from "../api/chat";
import { downloadFile } from "../api/files";
import FilePreview from "./FilePreview";

interface SourcePreviewModalProps {
    citation: Citation;
    fileName: string;
    fileType: string;
    onClose: () => void;
}

export default function SourcePreviewModal({ citation, fileName, fileType, onClose }: SourcePreviewModalProps) {
    const locationLabel =
        citation.page != null ? `${citation.page}페이지` : citation.sectionTitle ? citation.sectionTitle : "본문";

    return (
        <div
            className="fixed inset-0 z-[120] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
            onClick={onClose}
        >
            <div
                className="bg-white rounded-2xl w-full max-w-4xl h-[85vh] flex flex-col overflow-hidden shadow-2xl"
                onClick={(e) => e.stopPropagation()}
            >
                <div className="flex items-start justify-between gap-4 p-5 border-b border-outline-variant">
                    <div className="min-w-0">
                        <div className="flex items-center gap-2">
                            <FileText className="w-5 h-5 text-secondary shrink-0" />
                            <h3 className="text-lg font-bold text-on-surface truncate">{fileName}</h3>
                        </div>
                        <p className="text-xs text-outline mt-1 font-semibold">
                            {locationLabel}
                            {citation.score != null ? ` · 관련도 ${(citation.score * 100).toFixed(0)}%` : ""}
                        </p>
                        {citation.excerpt && (
                            <p className="mt-2 text-body-sm text-on-surface-variant bg-surface-container-low border-l-4 border-l-secondary rounded-r-lg px-3 py-2 line-clamp-3">
                                "{citation.excerpt}"
                            </p>
                        )}
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
                <div className="flex-1 overflow-hidden bg-surface-container-lowest">
                    <FilePreview
                        key={citation.fileId}
                        fileId={citation.fileId}
                        fileName={fileName}
                        fileType={fileType}
                        highlight={{ excerpt: citation.excerpt, sectionTitle: citation.sectionTitle, page: citation.page }}
                        className="h-full"
                    />
                </div>
            </div>
        </div>
    );
}
