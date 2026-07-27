import { useEffect, useRef, useState } from "react";
import { X, Download, FileText } from "lucide-react";
import { convertToHtml } from "mammoth/mammoth.browser";
import type { Citation } from "../api/chat";
import { fetchFileBlob, downloadFile } from "../api/files";

interface SourcePreviewModalProps {
    citation: Citation;
    fileName: string;
    fileType: string;
    onClose: () => void;
}

function normalize(text: string): string {
    return text.replace(/\s+/g, " ").trim();
}

function highlightExcerpt(container: HTMLElement, excerpt: string | null, sectionTitle: string | null): void {
    const candidates: string[] = [];
    if (excerpt) {
        const full = normalize(excerpt);
        if (full) candidates.push(full);
        if (full.length > 120) candidates.push(full.slice(0, 120));
        const firstSentence = full.split(/[.!?。\n]/)[0];
        if (firstSentence && firstSentence.length >= 10) candidates.push(firstSentence);
    }
    if (sectionTitle) candidates.push(normalize(sectionTitle));

    for (const needle of candidates) {
        if (!needle) continue;
        const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
        let node: Node | null;
        while ((node = walker.nextNode())) {
            const text = node.textContent ?? "";
            if (text.trim().length === 0) continue;
            if (normalize(text).includes(needle)) {
                const mark = document.createElement("mark");
                mark.className = "source-highlight";
                mark.textContent = text;
                node.parentNode?.replaceChild(mark, node);
                mark.scrollIntoView({ block: "center" });
                return;
            }
        }
    }
}

export default function SourcePreviewModal({ citation, fileName, fileType, onClose }: SourcePreviewModalProps) {
    const [status, setStatus] = useState<"loading" | "pdf" | "docx" | "txt" | "unsupported" | "error">("loading");
    const [blobUrl, setBlobUrl] = useState<string | null>(null);
    const [html, setHtml] = useState<string>("");
    const [text, setText] = useState<string>("");
    const docxRef = useRef<HTMLDivElement>(null);
    const txtRef = useRef<HTMLDivElement>(null);
    const type = fileType.toLowerCase();

    useEffect(() => {
        let active = true;
        let createdUrl: string | null = null;
        (async () => {
            try {
                const blob = await fetchFileBlob(citation.fileId);
                if (!active) return;
                if (type === "pdf") {
                    createdUrl = URL.createObjectURL(blob);
                    setBlobUrl(createdUrl);
                    setStatus("pdf");
                } else if (type === "docx") {
                    const arrayBuffer = await blob.arrayBuffer();
                    if (!active) return;
                    const result = await convertToHtml({ arrayBuffer });
                    if (!active) return;
                    setHtml(result.value);
                    setStatus("docx");
                } else if (type === "txt") {
                    const textContent = await blob.text();
                    if (!active) return;
                    setText(textContent);
                    setStatus("txt");
                } else {
                    setStatus("unsupported");
                }
            } catch {
                if (active) setStatus("error");
            }
        })();
        return () => {
            active = false;
            if (createdUrl) URL.revokeObjectURL(createdUrl);
        };
    }, [citation.fileId, type]);

    useEffect(() => {
        if (status === "docx" && docxRef.current) {
            highlightExcerpt(docxRef.current, citation.excerpt, citation.sectionTitle);
        }
        if (status === "txt" && txtRef.current) {
            highlightExcerpt(txtRef.current, citation.excerpt, citation.sectionTitle);
        }
    }, [status, citation.excerpt, citation.sectionTitle]);

    const locationLabel =
        citation.page != null ? `${citation.page}페이지` : citation.sectionTitle ? citation.sectionTitle : "본문";

    return (
        <div
            className="fixed inset-0 z-[120] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
            onClick={onClose}
        >
            <style>{`
        .docx-preview h1 { font-size: 1.5rem; font-weight: 700; margin: 1rem 0 0.5rem; }
        .docx-preview h2 { font-size: 1.25rem; font-weight: 700; margin: 1rem 0 0.5rem; }
        .docx-preview h3 { font-size: 1.1rem; font-weight: 600; margin: 0.75rem 0 0.5rem; }
        .docx-preview p { margin: 0.5rem 0; }
        .docx-preview ul { list-style: disc; padding-left: 1.5rem; margin: 0.5rem 0; }
        .docx-preview ol { list-style: decimal; padding-left: 1.5rem; margin: 0.5rem 0; }
        .docx-preview table { border-collapse: collapse; margin: 0.75rem 0; }
        .docx-preview td, .docx-preview th { border: 1px solid #d1d5db; padding: 4px 8px; }
        .docx-preview img { max-width: 100%; height: auto; }
        .docx-preview mark.source-highlight { background: #fde68a; padding: 1px 2px; border-radius: 2px; }
        .txt-preview mark.source-highlight { background: #fde68a; padding: 1px 2px; border-radius: 2px; }
      `}</style>
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
                    {status === "loading" && (
                        <div className="h-full flex items-center justify-center text-outline text-body-sm">
                            문서를 불러오는 중...
                        </div>
                    )}
                    {status === "pdf" && blobUrl && (
                        <iframe
                            title={fileName}
                            src={`${blobUrl}#page=${citation.page ?? 1}&view=FitH`}
                            className="w-full h-full border-0"
                        />
                    )}
                    {status === "docx" && (
                        <div
                            ref={docxRef}
                            className="docx-preview h-full overflow-y-auto px-8 py-6 bg-white text-on-surface leading-relaxed"
                            dangerouslySetInnerHTML={{ __html: html }}
                        />
                    )}
                    {status === "txt" && (
                        <div
                            ref={txtRef}
                            className="txt-preview h-full overflow-y-auto px-8 py-6 bg-white text-on-surface text-sm leading-relaxed font-mono whitespace-pre-wrap break-words"
                        >
                            {text.split("\n").map((line, i) => (
                                <div key={i}>{line === "" ? " " : line}</div>
                            ))}
                        </div>
                    )}
                    {(status === "unsupported" || status === "error") && (
                        <div className="h-full flex flex-col items-center justify-center gap-3 text-center px-6">
                            <p className="text-body-md text-on-surface-variant">
                                {status === "error" ? "문서를 불러오지 못했습니다." : "이 형식은 미리보기를 지원하지 않습니다."}
                            </p>
                            <button
                                type="button"
                                onClick={() => downloadFile(citation.fileId, fileName)}
                                className="px-4 py-2 bg-primary text-white text-sm font-bold rounded-xl hover:bg-opacity-95 cursor-pointer"
                            >
                                원본 다운로드
                            </button>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}