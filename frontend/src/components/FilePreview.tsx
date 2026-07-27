import { useEffect, useRef, useState } from "react";
import { convertToHtml } from "mammoth/mammoth.browser";
import * as XLSX from "xlsx";
import { fetchFileBlob, downloadFile } from "../api/files";

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
    className?: string;
}

type PreviewStatus = "loading" | "pdf" | "docx" | "txt" | "xlsx" | "unsupported" | "error";

interface SourcePosition {
    node: Text;
    offset: number;
}

function normalize(text: string): string {
    return text.replace(/\s+/g, " ").trim();
}

function escapeHtml(text: string): string {
    return text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
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

function blockContainer(node: Node): Element | null {
    let el = node.parentElement;
    while (el) {
        if (!getComputedStyle(el).display.startsWith("inline")) return el;
        if (!el.parentElement) return el;
        el = el.parentElement;
    }
    return null;
}

function collectNormalized(container: HTMLElement): { norm: string; positions: SourcePosition[] } {
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
    const positions: SourcePosition[] = [];
    let norm = "";
    let started = false;
    let prevBlock: Element | null = null;
    let pendingWhitespace = false;
    let whitespaceAnchor: SourcePosition | null = null;
    let current: Node | null;
    while ((current = walker.nextNode())) {
        const textNode = current as Text;
        const block = blockContainer(textNode);
        if (started && prevBlock && block !== prevBlock) {
            pendingWhitespace = true;
            if (!whitespaceAnchor) whitespaceAnchor = { node: textNode, offset: 0 };
        }
        prevBlock = block;
        const data = textNode.data;
        for (let k = 0; k < data.length; k++) {
            if (/\s/.test(data[k])) {
                if (started) {
                    pendingWhitespace = true;
                    if (!whitespaceAnchor) whitespaceAnchor = { node: textNode, offset: k };
                }
                continue;
            }
            if (pendingWhitespace) {
                norm += " ";
                positions.push(whitespaceAnchor ?? { node: textNode, offset: k });
                pendingWhitespace = false;
                whitespaceAnchor = null;
            }
            norm += data[k];
            positions.push({ node: textNode, offset: k });
            started = true;
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

function highlightExcerpt(container: HTMLElement, excerpt: string | null, sectionTitle: string | null): void {
    const candidates: string[] = [];
    if (excerpt) {
        const full = normalize(excerpt);
        if (full) candidates.push(full);
        if (full.length > 120) candidates.push(full.slice(0, 120));
        const firstSentence = full.split(/[.!?。]/)[0];
        if (firstSentence && firstSentence.length >= 10) candidates.push(firstSentence);
    }
    if (sectionTitle) candidates.push(normalize(sectionTitle));

    const { norm, positions } = collectNormalized(container);
    for (const needle of candidates) {
        if (!needle) continue;
        const idx = norm.indexOf(needle);
        if (idx < 0) continue;
        const start = positions[idx];
        const end = positions[idx + needle.length - 1];
        if (!start || !end) continue;
        if (wrapRange(container, start, end)) return;
    }
}

export default function FilePreview({ fileId, fileName, fileType, highlight, className }: FilePreviewProps) {
    const [status, setStatus] = useState<PreviewStatus>("loading");
    const [blobUrl, setBlobUrl] = useState<string | null>(null);
    const [html, setHtml] = useState<string>("");
    const [text, setText] = useState<string>("");
    const contentRef = useRef<HTMLDivElement>(null);
    const type = fileType.toLowerCase();

    useEffect(() => {
        let active = true;
        let createdUrl: string | null = null;
        (async () => {
            try {
                const blob = await fetchFileBlob(fileId);
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
                } else if (type === "xlsx") {
                    const arrayBuffer = await blob.arrayBuffer();
                    if (!active) return;
                    const workbook = XLSX.read(new Uint8Array(arrayBuffer), { type: "array" });
                    const rendered = workbook.SheetNames
                        .map((name) => `<h3 class="xlsx-sheet-name">${escapeHtml(name)}</h3>${XLSX.utils.sheet_to_html(workbook.Sheets[name])}`)
                        .join("");
                    if (!active) return;
                    setHtml(rendered);
                    setStatus("xlsx");
                } else if (type === "txt") {
                    const buffer = await blob.arrayBuffer();
                    if (!active) return;
                    setText(decodeTextBytes(buffer));
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
    }, [fileId, type]);

    useEffect(() => {
        const excerpt = highlight?.excerpt ?? null;
        const sectionTitle = highlight?.sectionTitle ?? null;
        if (!excerpt && !sectionTitle) return;
        if ((status === "docx" || status === "xlsx" || status === "txt") && contentRef.current) {
            highlightExcerpt(contentRef.current, excerpt, sectionTitle);
        }
    }, [status, highlight?.excerpt, highlight?.sectionTitle]);

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
            {status === "pdf" && blobUrl && (
                <iframe
                    title={fileName}
                    src={`${blobUrl}#page=${highlight?.page ?? 1}&view=FitH`}
                    className="w-full h-full border-0"
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
