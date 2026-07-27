import { useState } from "react";
import { Search, Loader2, FileText, MessageSquare, SearchX } from "lucide-react";
import { searchDocuments, type SearchResultItem } from "../api/search";
import { ApiError } from "../api/client";

interface SemanticSearchViewProps {
  onNavigateToChat: (docIds: string[]) => void;
}

function describeSearchError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 429) return "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.";
    if (err.status === 503) return "검색 서버에 일시적으로 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.";
    if (err.status === 502) return "검색 서버 응답에 문제가 발생했습니다. 다시 시도해 주세요.";
    if (err.status === 401) return "로그인이 필요합니다. 다시 로그인해 주세요.";
    if (err.status === 400) return err.message || "검색어가 올바르지 않습니다.";
  }
  return "검색 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.";
}

export default function SemanticSearchView({ onNavigateToChat }: SemanticSearchViewProps) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResultItem[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasSearched, setHasSearched] = useState(false);

  const runSearch = async () => {
    const trimmed = query.trim();
    if (!trimmed || isLoading) return;

    setIsLoading(true);
    setError(null);
    try {
      const response = await searchDocuments({ query: trimmed });
      setResults(response.items);
      setHasSearched(true);
    } catch (err) {
      setError(describeSearchError(err));
      setResults([]);
      setHasSearched(true);
    } finally {
      setIsLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      void runSearch();
    }
  };

  return (
    <div className="max-w-3xl mx-auto" id="semantic-search-view">
      <div className="mb-6">
        <h1 className="text-headline-sm font-bold text-on-surface mb-1">의미 기반 검색</h1>
        <p className="text-body-sm text-outline">
          자연어로 질문하면 답변 생성 없이 관련 구절이 있는 문서를 관련도순으로 찾아 드립니다.
        </p>
      </div>

      <div className="relative group mb-8">
        <Search className="w-5 h-5 absolute left-4 top-1/2 -translate-y-1/2 text-outline group-focus-within:text-primary transition-colors" />
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="예: 프로젝트 배포 절차가 어디에 있지?"
          className="w-full bg-surface-container-low border border-outline-variant rounded-full py-3.5 pl-12 pr-28 focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary text-body-md font-medium transition-all"
          id="semantic-search-input"
        />
        <button
          onClick={() => void runSearch()}
          disabled={isLoading || !query.trim()}
          className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-2 bg-primary text-white font-semibold text-label-md px-5 py-2 rounded-full transition-all disabled:opacity-40 disabled:cursor-not-allowed hover:bg-primary/90 cursor-pointer"
          id="semantic-search-submit"
        >
          {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
          검색
        </button>
      </div>

      {error && (
        <div className="bg-rose-50 border border-rose-200 text-rose-600 rounded-xl px-5 py-4 text-body-sm font-medium">
          {error}
        </div>
      )}

      {!error && isLoading && (
        <div className="flex flex-col items-center justify-center py-20 text-outline">
          <Loader2 className="w-8 h-8 animate-spin mb-3" />
          <p className="text-body-sm font-medium">문서를 검색하고 있습니다...</p>
        </div>
      )}

      {!error && !isLoading && hasSearched && results.length === 0 && (
        <div className="flex flex-col items-center justify-center py-20 text-outline">
          <SearchX className="w-10 h-10 mb-3" />
          <p className="text-body-md font-semibold text-on-surface-variant mb-1">검색 결과가 없습니다</p>
          <p className="text-body-sm">다른 표현으로 다시 검색하거나, 먼저 문서를 업로드해 주세요.</p>
        </div>
      )}

      {!error && !isLoading && results.length > 0 && (
        <ul className="space-y-3" id="semantic-search-results">
          {results.map((item) => (
            <li
              key={item.fileId}
              className="bg-white border border-outline-variant rounded-xl p-5 hover:shadow-sm transition-shadow"
            >
              <div className="flex items-start justify-between gap-4">
                <div className="flex items-start gap-3 min-w-0">
                  <FileText className="w-5 h-5 text-primary shrink-0 mt-0.5" />
                  <div className="min-w-0">
                    <p className="font-bold text-body-md text-on-surface truncate">{item.name}</p>
                    {item.matchedChunk && (
                      <p className="text-body-sm text-on-surface-variant mt-1.5 line-clamp-3">
                        {item.matchedChunk}
                      </p>
                    )}
                  </div>
                </div>
                <span
                  className="shrink-0 text-label-sm font-bold text-primary bg-primary/10 px-2.5 py-1 rounded-full"
                  title="유사도 점수"
                >
                  {Math.min(100, Math.max(0, Math.round(item.score * 100)))}%
                </span>
              </div>
              <div className="flex justify-end mt-3">
                <button
                  onClick={() => onNavigateToChat([String(item.fileId)])}
                  className="flex items-center gap-1.5 text-label-md font-semibold text-primary hover:bg-primary/10 px-3 py-1.5 rounded-lg transition-colors cursor-pointer"
                >
                  <MessageSquare className="w-4 h-4" />
                  이 문서로 채팅
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
