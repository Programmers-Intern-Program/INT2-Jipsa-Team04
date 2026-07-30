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

export interface PreviewLocation {
    excerpt?: string | null;
    sectionTitle?: string | null;
    page?: number | null;
}

interface FilePreviewProps {
    fileId: number;
    fileName: string;
    fileType: string;
    location?: PreviewLocation;
    className?: string;
}

type PreviewStatus = "loading" | "pdf" | "docx" | "txt" | "xlsx" | "unsupported" | "error";

interface SourcePosition {
    node: Text;
    offset: number;
}

interface GuidePosition {
    left: number;
    top: number;
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

function collectNormalized(container: HTMLElement): { normalized: string; positions: SourcePosition[] } {
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
    const positions: SourcePosition[] = [];
    let normalized = "";
    let current: Node | null;
    while ((current = walker.nextNode())) {
        const textNode = current as Text;
        for (let offset = 0; offset < textNode.data.length; offset++) {
            const value = textNode.data[offset].normalize("NFKC").replace(/[^\p{L}\p{N}]/gu, "");
            if (!value) continue;
            normalized += value;
            for (let index = 0; index < value.length; index++) {
                positions.push({ node: textNode, offset });
            }
        }
    }
    return { normalized, positions };
}

function locationCandidates(excerpt: string | null, sectionTitle: string | null): string[] {
    const candidates: string[] = [];
    if (excerpt) {
        const full = normalize(excerpt);
        if (full) candidates.push(full);
        candidates.push(
            ...excerpt
                .split(/[.!?。]\s*/)
                .map(normalize)
                .filter((sentence) => sentence.length >= 10)
                .sort((left, right) => right.length - left.length)
        );
        if (full.length > 160) candidates.push(full.slice(0, 160));
        if (full.length > 80) candidates.push(full.slice(0, 80));
    }
    if (sectionTitle) candidates.push(normalize(sectionTitle));
    return [...new Set(candidates.filter(Boolean))];
}

function findLocationRange(
    container: HTMLElement,
    excerpt: string | null,
    sectionTitle: string | null
): Range | null {
    const { normalized, positions } = collectNormalized(container);
    for (const candidate of locationCandidates(excerpt, sectionTitle)) {
        const startIndex = normalized.indexOf(candidate);
        if (startIndex < 0) continue;
        const start = positions[startIndex];
        const end = positions[startIndex + candidate.length - 1];
        if (!start || !end) continue;
        const range = document.createRange();
        range.setStart(start.node, start.offset);
        range.setEnd(end.node, end.offset + 1);
        return range;
    }
    return null;
}

function positionRange(
    container: HTMLElement,
    range: Range,
    horizontalAnchor: HTMLElement
): GuidePosition | null {
    const bounds = range.getBoundingClientRect();
    if (bounds.width === 0 && bounds.height === 0) return null;
    const containerBounds = container.getBoundingClientRect();
    const top = container.scrollTop + bounds.top - containerBounds.top - container.clientHeight / 2 + bounds.height / 2;
    const left = container.scrollLeft + bounds.left - containerBounds.left - container.clientWidth / 2 + bounds.width / 2;
    container.scrollTo({
        top: Math.max(0, top),
        left: Math.max(0, left),
        behavior: "auto",
    });
    const positionedBounds = range.getBoundingClientRect();
    const anchorBounds = horizontalAnchor.getBoundingClientRect();
    return {
        left: Math.min(
            Math.max(8, anchorBounds.left - containerBounds.left - 28),
            Math.max(8, container.clientWidth - 32)
        ),
        top: Math.min(
            Math.max(0, positionedBounds.top - containerBounds.top + positionedBounds.height / 2),
            container.clientHeight
        ),
    };
}

function pageContainsLocation(text: string, excerpt: string | null, sectionTitle: string | null): boolean {
    const normalizedText = normalize(text);
    return locationCandidates(excerpt, sectionTitle).some((candidate) => normalizedText.includes(candidate));
}

function LocationGuide({ position }: { position: GuidePosition }) {
    return (
        <div
            className="citation-location-guide pointer-events-none absolute z-30 flex -translate-y-1/2 items-center text-secondary"
            style={{ left: position.left, top: position.top }}
        >
            <div className="h-12 w-1 rounded-full bg-secondary shadow-[0_0_12px_rgba(0,121,140,0.35)]" />
            <ChevronRight className="-ml-0.5 h-6 w-6 stroke-[3]" />
        </div>
    );
}

function PdfPreview({ data, location }: { data: ArrayBuffer; location?: PreviewLocation }) {
    const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
    const [pageNumber, setPageNumber] = useState(1);
    const [targetPage, setTargetPage] = useState<number | null>(null);
    const [containerWidth, setContainerWidth] = useState(0);
    const [renderError, setRenderError] = useState(false);
    const [guidePosition, setGuidePosition] = useState<GuidePosition | null>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const textLayerRef = useRef<HTMLDivElement>(null);
    const excerpt = location?.excerpt ?? null;
    const sectionTitle = location?.sectionTitle ?? null;
    const requestedPage = location?.page ?? 1;

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
            const page = Math.min(Math.max(requestedPage, 1), pdf.numPages);
            setPageNumber(page);
            setTargetPage(null);
            setGuidePosition(null);
            if (!excerpt && !sectionTitle) {
                return;
            }
            const hasRequestedPage = location?.page != null;
            const order = hasRequestedPage
                ? [page]
                : Array.from({ length: pdf.numPages }, (_, index) => index + 1);
            for (const candidate of order) {
                try {
                    const pdfPage = await pdf.getPage(candidate);
                    const content = await pdfPage.getTextContent();
                    const text = content.items.map((item) => ("str" in item ? item.str : "")).join(" ");
                    if (pageContainsLocation(text, excerpt, sectionTitle)) {
                        if (active) {
                            setTargetPage(candidate);
                            setPageNumber(candidate);
                        }
                        return;
                    }
                } catch {
                    if (active) {
                        setTargetPage(null);
                        setGuidePosition(null);
                    }
                    return;
                }
            }
        };
        void resolvePage();
        return () => {
            active = false;
        };
    }, [pdf, excerpt, sectionTitle, requestedPage, location?.page]);

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
            try {
                renderTask = page.render({
                    canvasContext: context,
                    canvas,
                    viewport,
                    transform: outputScale === 1 ? undefined : [outputScale, 0, 0, outputScale, 0, 0],
                });
                await renderTask.promise;
            } catch {
                if (active) setRenderError(true);
                return;
            }
            if (!active) return;
            try {
                const textContent = await page.getTextContent();
                if (!active) return;
                textLayer = new TextLayer({ textContentSource: textContent, container: layer, viewport });
                await textLayer.render();
                if (!active || targetPage !== pageNumber) {
                    if (active) setGuidePosition(null);
                    return;
                }
                const range = findLocationRange(layer, excerpt, sectionTitle);
                const container = containerRef.current;
                if (range && container) {
                    setGuidePosition(positionRange(container, range, layer));
                } else {
                    setGuidePosition(null);
                }
            } catch {
                layer.replaceChildren();
                if (active) setGuidePosition(null);
            }
        };
        void renderPage().catch(() => {
            if (active) setRenderError(true);
        });
        return () => {
            active = false;
            renderTask?.cancel();
            textLayer?.cancel();
        };
    }, [pdf, pageNumber, containerWidth, targetPage, excerpt, sectionTitle]);

    const movePage = (page: number) => {
        setGuidePosition(null);
        setPageNumber(page);
    };

    if (renderError) {
        return <div className="h-full flex items-center justify-center text-outline text-body-sm">PDF를 렌더링하지 못했습니다.</div>;
    }

    return (
        <div className="relative h-full">
            <div ref={containerRef} className="h-full overflow-auto bg-surface-container-lowest">
                {pdf && (
                    <div className="sticky top-3 z-20 mx-auto mb-3 flex w-fit items-center gap-3 rounded-full border border-outline-variant bg-white/95 px-3 py-1.5 shadow-sm backdrop-blur">
                        <button
                            type="button"
                            onClick={() => movePage(Math.max(1, pageNumber - 1))}
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
                            onClick={() => movePage(Math.min(pdf.numPages, pageNumber + 1))}
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
            {guidePosition && <LocationGuide position={guidePosition} />}
        </div>
    );
}

export default function FilePreview({
    fileId,
    fileName,
    fileType,
    location,
    className,
}: FilePreviewProps) {
    const type = fileType.toLowerCase();
    const [status, setStatus] = useState<PreviewStatus>(() => (isRenderable(type) ? "loading" : "unsupported"));
    const [pdfData, setPdfData] = useState<ArrayBuffer | null>(null);
    const [html, setHtml] = useState<string>("");
    const [text, setText] = useState<string>("");
    const [guidePosition, setGuidePosition] = useState<GuidePosition | null>(null);
    const [layoutVersion, setLayoutVersion] = useState(0);
    const previewContainerRef = useRef<HTMLDivElement>(null);
    const contentRef = useRef<HTMLDivElement>(null);
    const excerpt = location?.excerpt ?? null;
    const sectionTitle = location?.sectionTitle ?? null;

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
        const container = previewContainerRef.current;
        if (!container) return;
        const observer = new ResizeObserver(() => {
            setLayoutVersion((current) => current + 1);
        });
        observer.observe(container);
        return () => observer.disconnect();
    }, [status]);

    useEffect(() => {
        const frame = requestAnimationFrame(() => {
            if (status !== "docx" && status !== "xlsx" && status !== "txt") {
                setGuidePosition(null);
                return;
            }
            if (!excerpt && !sectionTitle) {
                setGuidePosition(null);
                return;
            }
            const content = contentRef.current;
            const container = previewContainerRef.current;
            if (!content || !container) {
                setGuidePosition(null);
                return;
            }
            const range = findLocationRange(content, excerpt, sectionTitle);
            setGuidePosition(range ? positionRange(container, range, content) : null);
        });
        return () => cancelAnimationFrame(frame);
    }, [status, excerpt, sectionTitle, layoutVersion]);

    return (
        <div className={`relative ${className ?? "h-full"}`}>
            <style>{`
        @keyframes citation-guide-arrive {
          0% { opacity: 0; transform: translate(-10px, -50%); }
          45% { opacity: 1; transform: translate(3px, -50%); }
          100% { opacity: 1; transform: translate(0, -50%); }
        }
        .citation-location-guide { animation: citation-guide-arrive 700ms ease-out both; }
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
      `}</style>
            {status === "loading" && (
                <div className="h-full flex items-center justify-center text-outline text-body-sm">
                    문서를 불러오는 중...
                </div>
            )}
            {status === "pdf" && pdfData && <PdfPreview data={pdfData} location={location} />}
            {(status === "docx" || status === "xlsx") && (
                <div
                    ref={previewContainerRef}
                    className="h-full overflow-auto bg-white"
                >
                    <div
                        ref={contentRef}
                        className={`preview-rich min-h-full px-8 py-6 text-on-surface leading-relaxed ${
                            status === "xlsx" ? "w-max min-w-full" : "w-full"
                        }`}
                        dangerouslySetInnerHTML={{ __html: html }}
                    />
                </div>
            )}
            {status === "txt" && (
                <div
                    ref={previewContainerRef}
                    className="h-full overflow-auto bg-white"
                >
                    <div
                        ref={contentRef}
                        className="preview-plain min-h-full min-w-full px-8 py-6 text-on-surface text-sm leading-relaxed font-mono whitespace-pre-wrap break-words"
                    >
                        {text.split("\n").map((line, index) => (
                            <div key={index}>{line === "" ? " " : line}</div>
                        ))}
                    </div>
                </div>
            )}
            {guidePosition && <LocationGuide position={guidePosition} />}
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
