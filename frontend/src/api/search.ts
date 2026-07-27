import { apiFetch } from "./client";

export interface SearchResultItem {
    fileId: number;
    name: string;
    matchedChunk: string | null;
    score: number;
}

export interface SearchResponse {
    items: SearchResultItem[];
}

export interface SearchRequest {
    query: string;
    folderId?: number;
}

// POST /api/v1/search — 자연어 의미 기반 문서 검색.
// 답변 생성 없이 관련 구절이 있는 문서를 관련도 점수순으로 돌려준다.
export function searchDocuments(request: SearchRequest): Promise<SearchResponse> {
    return apiFetch<SearchResponse>("/search", {
        method: "POST",
        body: request,
    });
}
