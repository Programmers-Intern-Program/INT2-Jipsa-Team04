import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { X, Download, FileText, MapPin, Sparkles } from "lucide-react";
import type { Citation } from "../api/chat";
import { downloadFile } from "../api/files";
import FilePreview from "./FilePreview";

interface EmphasisRange {
    start: number;
    end: number;
}

interface EvidenceBlock {
    text: string;
    start: number;
}

interface SourcePreviewModalProps {
    citation: Citation;
    fileName: string;
    fileType: string;
    documentOnly?: boolean;
    contextText?: string;
    onClose: () => void;
}

const EMPHASIS_STOP_WORDS = new Set([
    "그리고",
    "그러나",
    "따라서",
    "대한",
    "대해",
    "위한",
    "통해",
    "있는",
    "없는",
    "한다",
    "했다",
    "있다",
    "문서",
    "결과",
    "관련",
]);

function escapePattern(text: string): string {
    return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function buildEmphasisCandidates(contextText: string): string[] {
    const tokens = contextText.match(/[\p{L}\p{N}][\p{L}\p{N}._/-]*/gu) ?? [];
    const candidates: string[] = [];
    for (let size = Math.min(4, tokens.length); size >= 2; size--) {
        for (let index = 0; index <= tokens.length - size; index++) {
            candidates.push(tokens.slice(index, index + size).join(" "));
        }
    }
    for (const token of tokens) {
        const normalized = token.toLowerCase();
        if (EMPHASIS_STOP_WORDS.has(normalized)) continue;
        if (/\d/.test(token) || token.length >= 4) candidates.push(token);
    }
    return [...new Set(candidates)].sort((left, right) => right.length - left.length);
}

function findEmphasisRanges(text: string, contextText: string): EmphasisRange[] {
    const ranges: EmphasisRange[] = [];
    for (const candidate of buildEmphasisCandidates(contextText)) {
        const parts = candidate.split(/\s+/).filter(Boolean);
        if (parts.length === 0) continue;
        const pattern = parts.map(escapePattern).join("[\\s·,:;()\"'“”‘’/~-]+");
        const match = new RegExp(pattern, "iu").exec(text);
        if (!match) continue;
        const range = { start: match.index, end: match.index + match[0].length };
        if (ranges.some((existing) => range.start < existing.end && range.end > existing.start)) continue;
        ranges.push(range);
        if (ranges.length >= 4) break;
    }
    return ranges.sort((left, right) => left.start - right.start);
}

function splitEvidenceText(excerpt: string): string[] {
    return excerpt
        .replace(/\r\n?/g, "\n")
        .split(/\n+|(?<=[.!?。])\s+/u)
        .map((block) => block.trim())
        .filter(Boolean);
}

function buildEvidenceBlocks(excerpt: string): { blocks: EvidenceBlock[]; searchableText: string } {
    const texts = splitEvidenceText(excerpt);
    let offset = 0;
    const blocks = texts.map((text) => {
        const block = { text, start: offset };
        offset += text.length + 1;
        return block;
    });
    return {
        blocks,
        searchableText: texts.join("\u0000"),
    };
}

function rangesForBlock(block: EvidenceBlock, ranges: EmphasisRange[]): EmphasisRange[] {
    const blockEnd = block.start + block.text.length;
    return ranges
        .filter((range) => range.start < blockEnd && range.end > block.start)
        .map((range) => ({
            start: Math.max(0, range.start - block.start),
            end: Math.min(block.text.length, range.end - block.start),
        }));
}

function renderEmphasizedText(text: string, ranges: EmphasisRange[]): ReactNode[] {
    const nodes: ReactNode[] = [];
    let cursor = 0;
    ranges.forEach((range, index) => {
        if (range.start > cursor) nodes.push(text.slice(cursor, range.start));
        nodes.push(
            <strong key={`${range.start}-${range.end}-${index}`} className="font-extrabold text-on-surface">
                {text.slice(range.start, range.end)}
            </strong>
        );
        cursor = range.end;
    });
    if (cursor < text.length) nodes.push(text.slice(cursor));
    return nodes;
}

function EvidenceCard({
    excerpt,
    contextText,
    locationLabel,
}: {
    excerpt: string;
    contextText: string;
    locationLabel: string;
}) {
    const scrollRef = useRef<HTMLDivElement>(null);
    const [fades, setFades] = useState({ top: false, bottom: false });
    const evidence = useMemo(() => buildEvidenceBlocks(excerpt), [excerpt]);
    const emphasisRanges = useMemo(
        () => findEmphasisRanges(evidence.searchableText, contextText),
        [evidence.searchableText, contextText]
    );
    const formattedBlocks = useMemo(
        () => evidence.blocks.map((block) => ({
            text: block.text,
            ranges: rangesForBlock(block, emphasisRanges),
        })),
        [evidence.blocks, emphasisRanges]
    );
    const updateFades = useCallback(() => {
        const element = scrollRef.current;
        if (!element) return;
        const next = {
            top: element.scrollTop > 2,
            bottom: element.scrollHeight - element.scrollTop - element.clientHeight > 2,
        };
        setFades((current) => current.top === next.top && current.bottom === next.bottom ? current : next);
    }, []);

    useEffect(() => {
        const element = scrollRef.current;
        if (!element) return;
        const frame = requestAnimationFrame(updateFades);
        const observer = new ResizeObserver(updateFades);
        observer.observe(element);
        return () => {
            cancelAnimationFrame(frame);
            observer.disconnect();
        };
    }, [formattedBlocks, updateFades]);

    return (
        <div className="relative mt-3 overflow-hidden rounded-xl border border-secondary/20 bg-secondary/[0.035]">
            {fades.top && (
                <div className="pointer-events-none absolute inset-x-0 top-0 z-10 h-8 bg-gradient-to-b from-white via-white/80 to-transparent" />
            )}
            <div
                ref={scrollRef}
                onScroll={updateFades}
                className="max-h-[52vh] overflow-y-auto px-4 py-4 text-sm leading-7 text-on-surface"
            >
                <div className="mb-3 flex items-center gap-1.5 text-[11px] font-bold text-secondary/80">
                    <MapPin className="h-3 w-3" />
                    {locationLabel}
                </div>
                <div className="border-l-2 border-secondary/30 pl-3">
                    {formattedBlocks.map((block, index) => {
                        const keyValue = block.text.match(/^([^:]{1,24}):\s*(.+)$/u);
                        if (keyValue) {
                            const valueOffset = block.text.indexOf(keyValue[2]);
                            const valueRanges = block.ranges
                                .filter((range) => range.end > valueOffset)
                                .map((range) => ({
                                    start: Math.max(0, range.start - valueOffset),
                                    end: Math.min(keyValue[2].length, range.end - valueOffset),
                                }));
                            return (
                                <div key={`${index}-${block.text}`} className="mb-3 last:mb-0">
                                    <div className="mb-0.5 text-[11px] font-bold text-on-surface-variant">
                                        {keyValue[1].trim()}
                                    </div>
                                    <div>{renderEmphasizedText(keyValue[2], valueRanges)}</div>
                                </div>
                            );
                        }
                        return (
                            <p
                                key={`${index}-${block.text}`}
                                className="mb-3 break-words last:mb-0"
                            >
                                {renderEmphasizedText(block.text, block.ranges)}
                            </p>
                        );
                    })}
                </div>
            </div>
            {fades.bottom && (
                <div className="pointer-events-none absolute inset-x-0 bottom-0 z-10 h-10 bg-gradient-to-t from-white via-white/80 to-transparent" />
            )}
        </div>
    );
}

export default function SourcePreviewModal({
    citation,
    fileName,
    fileType,
    documentOnly = false,
    contextText = "",
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
                            <EvidenceCard
                                excerpt={citation.excerpt}
                                contextText={contextText}
                                locationLabel={locationLabel}
                            />
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
