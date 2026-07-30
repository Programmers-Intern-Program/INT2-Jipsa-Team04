import { useEffect, useRef, useState } from "react";
import { convertToHtml } from "mammoth/mammoth.browser";
import * as XLSX from "xlsx";
import DOMPurify from "dompurify";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { getDocument, GlobalWorkerOptions, TextLayer, type PDFDocumentProxy, type RenderTask } from "pdfjs-dist";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import "pdfjs-dist/web/pdf_viewer.css";
import { fetchFileBlob, downloadFile } from "../api/files";

GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

export interface PreviewHighlight {
    excerpt?: string | null;
    sectionTitle?: string | null;
    page?: number | null;
}

interface FilePreviewProps {
    fileId: number;
    fileName: string;
    fileType: string;
    highlight?: PreviewHighlight;
    onHighlightStatusChange?: (status: HighlightStatus) => void;
    className?: string;
}

type PreviewStatus = "loading" | "pdf" | "docx" | "txt" | "xlsx" | "unsupported" | "error";
export type HighlightStatus = "found" | "not-found" | "unavailable";

interface SourcePosition {
    node: Text;
    offset: number;
}

function normalize(text: string): string {
    return text.normalize("NFKC").replace(/[^\p{L}\p{N}]/gu, "");
}

function escapeHtml(text: string): string {
    return text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

function isRenderable(type: string): boolean {
    return type === "pdf" || type === "docx" || type === "xlsx" || type === "txt";
}

function decodeTextBytes(buffer: ArrayBuffer): string {
    const bytes = new Uint8Array(buffer);
    if (bytes.length >= 3 && bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) {
        return new TextDecoder("utf-8").decode(buffer);
    }
    if (bytes.length >= 2 && bytes[0] === 0xff && bytes[1] === 0xfe) {
        return new TextDecoder("utf-16le").decode(buffer);
    }
    if (bytes.length >= 2 && bytes[0] === 0xfe && bytes[1] === 0xff) {
        return new TextDecoder("utf-16be").decode(buffer);
    }
    try {
        return new TextDecoder("utf-8", { fatal: true }).decode(buffer);
    } catch {
        return new TextDecoder("euc-kr").decode(buffer);
    }
}

function collectNormalized(container: HTMLElement): { norm: string; positions: SourcePosition[] } {
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
    const positions: SourcePosition[] = [];
    let norm = "";
    let current: Node | null;
    while ((current = walker.nextNode())) {
        const textNode = current as Text;
        const data = textNode.data;
        for (let k = 0; k < data.length; k++) {
            const normalized = data[k].normalize("NFKC").replace(/[^\p{L}\p{N}]/gu, "");
            if (!normalized) {
                continue;
            }
            norm += normalized;
            for (let index = 0; index < normalized.length; index++) {
                positions.push({ node: textNode, offset: k });
            }
        }
    }
    return { norm, positions };
}

function wrapRange(container: HTMLElement, start: SourcePosition, end: SourcePosition): boolean {
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
    const nodes: Text[] = [];
    let collecting = false;
    let current: Node | null;
    while ((current = walker.nextNode())) {
        const textNode = current as Text;
        if (textNode === start.node) collecting = true;
        if (collecting) nodes.push(textNode);
        if (textNode === end.node) break;
    }
    let firstMark: HTMLElement | null = null;
    for (const textNode of nodes) {
        const from = textNode === start.node ? start.offset : 0;
        const to = textNode === end.node ? end.offset + 1 : textNode.data.length;
        if (to <= from) continue;
        if (textNode.data.slice(from, to).trim().length === 0) continue;
        const range = document.createRange();
        range.setStart(textNode, from);
        range.setEnd(textNode, to);
        const mark = document.createElement("mark");
        mark.className = "source-highlight";
        range.surroundContents(mark);
        if (!firstMark) firstMark = mark;
    }
    if (firstMark) {
        firstMark.scrollIntoView({ block: "center" });
        return true;
    }
    return false;
}

function highlightCandidates(excerpt: string | null, sectionTitle: string | null): string[] {
    const candidates: string[] = [];
    if (excerpt) {
        const full = normalize(excerpt);
        if (full) candidates.push(full);
        const sentences = excerpt
            .split(/[.!?。]\s*/)
            .map(normalize)
            .filter((sentence) => sentence.length >= 10)
            .sort((left, right) => right.length - left.length);
        candidates.push(...sentences);
        if (full.length > 160) candidates.push(full.slice(0, 160));
        if (full.length > 80) candidates.push(full.slice(0, 80));
    }
    if (sectionTitle) candidates.push(normalize(sectionTitle));
    return [...new Set(candidates.filter(Boolean))];
}

function clearHighlights(container: HTMLElement) {
    for (const mark of Array.from(container.querySelectorAll("mark.source-highlight"))) {
        mark.replaceWith(document.createTextNode(mark.textContent ?? ""));
    }
    container.normalize();
}

function highlightExcerpt(container: HTMLElement, excerpt: string | null, sectionTitle: string | null): boolean {
    clearHighlights(container);
    const candidates = highlightCandidates(excerpt, sectionTitle);

    const { norm, positions } = collectNormalized(container);
    for (const needle of candidates) {
        if (!needle) continue;
        const idx = norm.indexOf(needle);
        if (idx < 0) continue;
        const start = positions[idx];
        const end = positions[idx + needle.length - 1];
        if (!start || !end) continue;
        if (wrapRange(container, start, end)) return true;
    }
    return false;
}

function pdfPageTextMatches(text: string, highlight: PreviewHighlight): boolean {
    const normalizedText = normalize(text);
    return highlightCandidates(highlight.excerpt ?? null, highlight.sectionTitle ?? null)
        .some((candidate) => normalizedText.includes(candidate));
}

function PdfPreview({
    data,
    highlight,
    onHighlightStatusChange,
}: {
    data: ArrayBuffer;
    highlight?: PreviewHighlight;
    onHighlightStatusChange?: (status: HighlightStatus) => void;
}) {
    const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
    const [pageNumber, setPageNumber] = useState(1);
    const [containerWidth, setContainerWidth] = useState(0);
    const [renderError, setRenderError] = useState(false);
    const containerRef = useRef<HTMLDivElement>(null);
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const textLayerRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        let active = true;
        const loadingTask = getDocument({ data: data.slice(0) });
        loadingTask.promise
            .then((document) => {
                if (!active) {
                    void loadingTask.destroy();
                    return;
                }
                setPdf(document);
            })
            .catch(() => {
                if (active) setRenderError(true);
            });
        return () => {
            active = false;
            void loadingTask.destroy();
        };
    }, [data]);

    useEffect(() => {
        const container = containerRef.current;
        if (!container) return;
        const observer = new ResizeObserver(([entry]) => setContainerWidth(entry.contentRect.width));
        observer.observe(container);
        return () => observer.disconnect();
    }, []);

    useEffect(() => {
        if (!pdf) return;
        let active = true;
        const resolvePage = async () => {
            const hasTarget = Boolean(highlight?.excerpt || highlight?.sectionTitle);
            const requestedPage = Math.min(Math.max(highlight?.page ?? 1, 1), pdf.numPages);
            if (!hasTarget) {
                setPageNumber(requestedPage);
                onHighlightStatusChange?.("unavailable");
                return;
            }
            const order = [
                requestedPage,
                ...Array.from({ length: pdf.numPages }, (_, index) => index + 1)
                    .filter((page) => page !== requestedPage),
            ];
            for (const candidatePage of order) {
                const page = await pdf.getPage(candidatePage);
                const content = await page.getTextContent();
                const pageText = content.items
                    .map((item) => ("str" in item ? item.str : ""))
                    .join(" ");
                if (pdfPageTextMatches(pageText, highlight ?? {})) {
                    if (active) setPageNumber(candidatePage);
                    return;
                }
            }
            if (active) {
                setPageNumber(requestedPage);
                onHighlightStatusChange?.("not-found");
            }
        };
        void resolvePage();
        return () => {
            active = false;
        };
    }, [pdf, highlight, onHighlightStatusChange]);

    useEffect(() => {
        if (!pdf || containerWidth <= 0) return;
        let active = true;
        let textLayer: TextLayer | null = null;
        let renderTask: RenderTask | null = null;
        const renderPage = async () => {
            const page = await pdf.getPage(pageNumber);
            if (!active) return;
            const originalViewport = page.getViewport({ scale: 1 });
            const scale = Math.min(2, Math.max(0.5, (containerWidth - 32) / originalViewport.width));
            const viewport = page.getViewport({ scale });
            const canvas = canvasRef.current;
            const layer = textLayerRef.current;
            if (!canvas || !layer) return;
            const outputScale = window.devicePixelRatio || 1;
            canvas.width = Math.floor(viewport.width * outputScale);
            canvas.height = Math.floor(viewport.height * outputScale);
            canvas.style.width = `${viewport.width}px`;
            canvas.style.height = `${viewport.height}px`;
            const context = canvas.getContext("2d");
            if (!context) return;
            layer.replaceChildren();
            layer.style.width = `${viewport.width}px`;
            layer.style.height = `${viewport.height}px`;
            layer.style.setProperty("--scale-factor", String(viewport.scale));
            renderTask = page.render({
                canvasContext: context,
                canvas,
                viewport,
                transform: outputScale === 1 ? undefined : [outputScale, 0, 0, outputScale, 0, 0],
            });
            await renderTask.promise;
            if (!active) return;
            const textContent = await page.getTextContent();
            if (!active) return;
            textLayer = new TextLayer({ textContentSource: textContent, container: layer, viewport });
            await textLayer.render();
            if (!active) return;
            if (highlight?.excerpt || highlight?.sectionTitle) {
                const found = highlightExcerpt(
                    layer,
                    highlight.excerpt ?? null,
                    highlight.sectionTitle ?? null
                );
                onHighlightStatusChange?.(found ? "found" : "not-found");
            } else {
                onHighlightStatusChange?.("unavailable");
            }
        };
        void renderPage().catch(() => {
            if (active) {
                setRenderError(true);
                onHighlightStatusChange?.(
                    highlight?.excerpt || highlight?.sectionTitle ? "not-found" : "unavailable"
                );
            }
        });
        return () => {
            active = false;
            renderTask?.cancel();
            textLayer?.cancel();
        };
    }, [pdf, pageNumber, containerWidth, highlight, onHighlightStatusChange]);

    if (renderError) {
        return <div className="h-full flex items-center justify-center text-outline text-body-sm">PDF를 렌더링하지 못했습니다.</div>;
    }

    return (
        <div ref={containerRef} className="relative h-full overflow-auto bg-surface-container-lowest">
            {pdf && (
                <div className="sticky top-3 z-20 mx-auto mb-3 flex w-fit items-center gap-3 rounded-full border border-outline-variant bg-white/95 px-3 py-1.5 shadow-sm backdrop-blur">
                    <button
                        type="button"
                        onClick={() => setPageNumber((current) => Math.max(1, current - 1))}
                        disabled={pageNumber <= 1}
                        className="rounded-full p-1 text-outline hover:bg-surface-container disabled:opacity-30 cursor-pointer"
                    >
                        <ChevronLeft className="h-4 w-4" />
                    </button>
                    <span className="min-w-16 text-center text-xs font-bold text-on-surface">
                        {pageNumber} / {pdf.numPages}
                    </span>
                    <button
                        type="button"
                        onClick={() => setPageNumber((current) => Math.min(pdf.numPages, current + 1))}
                        disabled={pageNumber >= pdf.numPages}
                        className="rounded-full p-1 text-outline hover:bg-surface-container disabled:opacity-30 cursor-pointer"
                    >
                        <ChevronRight className="h-4 w-4" />
                    </button>
                </div>
            )}
            <div className="relative mx-auto mb-6 w-fit bg-white shadow-lg">
                <canvas ref={canvasRef} className="block" />
                <div ref={textLayerRef} className="textLayer absolute inset-0" />
            </div>
        </div>
    );
}

export default function FilePreview({
    fileId,
    fileName,
    fileType,
    highlight,
    onHighlightStatusChange,
    className,
}: FilePreviewProps) {
    const type = fileType.toLowerCase();
    const [status, setStatus] = useState<PreviewStatus>(() => (isRenderable(type) ? "loading" : "unsupported"));
    const [pdfData, setPdfData] = useState<ArrayBuffer | null>(null);
    const [html, setHtml] = useState<string>("");
    const [text, setText] = useState<string>("");
    const contentRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (!isRenderable(type)) return;
        let active = true;
        (async () => {
            try {
                const blob = await fetchFileBlob(fileId);
                if (!active) return;
                if (type === "pdf") {
                    const arrayBuffer = await blob.arrayBuffer();
                    if (!active) return;
                    setPdfData(arrayBuffer);
                    setStatus("pdf");
                } else if (type === "docx") {
                    const arrayBuffer = await blob.arrayBuffer();
                    if (!active) return;
                    const result = await convertToHtml({ arrayBuffer });
                    if (!active) return;
                    setHtml(DOMPurify.sanitize(result.value));
                    setStatus("docx");
                } else if (type === "xlsx") {
                    const arrayBuffer = await blob.arrayBuffer();
                    if (!active) return;
                    const workbook = XLSX.read(new Uint8Array(arrayBuffer), { type: "array" });
                    const rendered = workbook.SheetNames
                        .map((name) => `<h3 class="xlsx-sheet-name">${escapeHtml(name)}</h3>${XLSX.utils.sheet_to_html(workbook.Sheets[name])}`)
                        .join("");
                    if (!active) return;
                    setHtml(DOMPurify.sanitize(rendered));
                    setStatus("xlsx");
                } else if (type === "txt") {
                    const buffer = await blob.arrayBuffer();
                    if (!active) return;
                    setText(decodeTextBytes(buffer));
                    setStatus("txt");
                }
            } catch {
                if (active) setStatus("error");
            }
        })();
        return () => {
            active = false;
        };
    }, [fileId, type]);

    useEffect(() => {
        const excerpt = highlight?.excerpt ?? null;
        const sectionTitle = highlight?.sectionTitle ?? null;
        if (!excerpt && !sectionTitle) {
            onHighlightStatusChange?.("unavailable");
            return;
        }
        if ((status === "docx" || status === "xlsx" || status === "txt") && contentRef.current) {
            const found = highlightExcerpt(contentRef.current, excerpt, sectionTitle);
            onHighlightStatusChange?.(found ? "found" : "not-found");
        }
    }, [status, highlight?.excerpt, highlight?.sectionTitle, onHighlightStatusChange]);

    useEffect(() => {
        if (status !== "unsupported" && status !== "error") return;
        onHighlightStatusChange?.(
            highlight?.excerpt || highlight?.sectionTitle ? "not-found" : "unavailable"
        );
    }, [status, highlight?.excerpt, highlight?.sectionTitle, onHighlightStatusChange]);

    return (
        <div className={className ?? "h-full"}>
            <style>{`
        .preview-rich h1 { font-size: 1.5rem; font-weight: 700; margin: 1rem 0 0.5rem; }
        .preview-rich h2 { font-size: 1.25rem; font-weight: 700; margin: 1rem 0 0.5rem; }
        .preview-rich h3 { font-size: 1.1rem; font-weight: 600; margin: 0.75rem 0 0.5rem; }
        .preview-rich p { margin: 0.5rem 0; }
        .preview-rich ul { list-style: disc; padding-left: 1.5rem; margin: 0.5rem 0; }
        .preview-rich ol { list-style: decimal; padding-left: 1.5rem; margin: 0.5rem 0; }
        .preview-rich table { border-collapse: collapse; margin: 0.75rem 0; }
        .preview-rich td, .preview-rich th { border: 1px solid #d1d5db; padding: 4px 8px; }
        .preview-rich img { max-width: 100%; height: auto; }
        .preview-rich .xlsx-sheet-name { font-size: 0.95rem; font-weight: 700; margin: 1rem 0 0.5rem; color: #059669; }
        .preview-rich mark.source-highlight { background: #fde68a; padding: 1px 2px; border-radius: 2px; }
        .preview-plain mark.source-highlight { background: #fde68a; padding: 1px 2px; border-radius: 2px; }
      `}</style>
            {status === "loading" && (
                <div className="h-full flex items-center justify-center text-outline text-body-sm">
                    문서를 불러오는 중...
                </div>
            )}
            {status === "pdf" && pdfData && (
                <PdfPreview
                    data={pdfData}
                    highlight={highlight}
                    onHighlightStatusChange={onHighlightStatusChange}
                />
            )}
            {(status === "docx" || status === "xlsx") && (
                <div
                    ref={contentRef}
                    className="preview-rich h-full overflow-auto px-8 py-6 bg-white text-on-surface leading-relaxed"
                    dangerouslySetInnerHTML={{ __html: html }}
                />
            )}
            {status === "txt" && (
                <div
                    ref={contentRef}
                    className="preview-plain h-full overflow-y-auto px-8 py-6 bg-white text-on-surface text-sm leading-relaxed font-mono whitespace-pre-wrap break-words"
                >
                    {text.split("\n").map((line, i) => (
                        <div key={i}>{line === "" ? " " : line}</div>
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
                        onClick={() => downloadFile(fileId, fileName)}
                        className="px-4 py-2 bg-primary text-white text-sm font-bold rounded-xl hover:bg-opacity-95 cursor-pointer"
                    >
                        원본 다운로드
                    </button>
                </div>
            )}
        </div>
    );
}
