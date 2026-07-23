import { useState, useEffect } from "react";
import { motion } from "motion/react";
import {
  Sparkles,
  ArrowRight,
  Folder,
  FileText,
  TrendingUp,
  FileSpreadsheet,
  Check,
  Merge,
  Highlighter
} from "lucide-react";
import type { Document } from "../types";
import { formatBytes } from "../utils/formatBytes";
import { formatDateTime } from "../utils/formatDateTime";
import { mockFolders } from "../mocks/mockData";
import { isDescendantOrSelf } from "../utils/folderTree";
import { listFolders } from "../api/folders";

interface DashboardViewProps {
  documents: Document[];
  onNavigateToChat: (docIds: string[]) => void;
  onNavigateToTab: (tab: string) => void;
}

export default function DashboardView({ documents, onNavigateToChat, onNavigateToTab }: DashboardViewProps) {
  const [completedActions, setCompletedActions] = useState<string[]>([]);
  const [loadingAction, setLoadingAction] = useState<string | null>(null);
  const [folders, setFolders] = useState(mockFolders);
  useEffect(() => {
    listFolders().then(setFolders).catch(() => {});
  }, []);

  const totalCount = documents.length;
  const starredCount = documents.filter(d => d.star).length;
  const processingCount = documents.filter(d => d.status === "PROCESSING" || d.status === "UPLOADED").length;
  const rootFolders = folders.filter(f => f.parentFolderId === null);
  const folderCounts = rootFolders
      .map(f => ({ name: f.name, count: documents.filter(d => d.folderId !== null && isDescendantOrSelf(d.folderId, f.folderId, folders)).length }))
      .sort((a, b) => b.count - a.count);
  const topFolder = folderCounts[0] ?? { name: "미분류", count: documents.filter(d => d.folderId === null).length };
  const topPercent = totalCount > 0 ? Math.round((topFolder.count / totalCount) * 100) : 0;

  const handleRunSummary = (id: string) => {
    setLoadingAction(id);
    setTimeout(() => {
      setLoadingAction(null);
      setCompletedActions([...completedActions, id]);
      alert("AI가 '2024년 4분기 경영 전략.pdf' 문서의 3줄 요약을 성공적으로 추출하였습니다.\n\n요약 결과:\n1. 클라우드 현대화 12억 원 전액 집행 완료.\n2. 전 부서 업무 자동화 솔루션 45% 도입 진행.\n3. 해외 마케팅 예산 15% 부족에 대한 비상 승인 절차 개시.");
    }, 1500);
  };

  const handleApplyHighlight = (id: string) => {
    setLoadingAction(id);
    setTimeout(() => {
      setLoadingAction(null);
      setCompletedActions([...completedActions, id]);
      alert("AI가 '5월 예산 집행 현황.xlsx' 문서에서 핵심 수치를 자동 하이라이트했습니다.\n\n하이라이트된 항목:\n- 글로벌 마케팅 예산 약 15%(1억 2천만 원) 부족\n- 클라우드 인프라 12억 원 전액 집행 완료");
    }, 1500);
  };

  const handleMergeDocs = (id: string) => {
    setLoadingAction(id);
    setTimeout(() => {
      setLoadingAction(null);
      setCompletedActions([...completedActions, id]);
      alert("중복된 3개의 프로젝트 알파 관련 임시 메모를 분석하여 '프로젝트 알파 핵심 기획안_통합본.docx' 파일로 영구 통합 완료했습니다.");
    }, 1800);
  };

  return (
    <motion.div 
      initial={{ opacity: 0, y: 15 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -15 }}
      className="space-y-8"
      id="dashboard-view-container"
    >
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-6" id="dashboard-header-block">
        <div>
          <h2 className="text-3xl font-bold tracking-tight text-on-surface font-sans" id="dashboard-main-title">오늘의 분석 요약</h2>
          <p className="text-body-md text-on-surface-variant font-sans mt-1">AI가 최근에 분류한 문서들의 실시간 통계 및 분석 현황입니다.</p>
        </div>
        <button 
          onClick={() => onNavigateToTab("documents")}
          className="text-primary font-semibold text-label-md flex items-center gap-1 hover:underline cursor-pointer group transition-all"
          id="btn-view-all-reports"
        >
          전체 리포트 보기 
          <ArrowRight className="w-4 h-4 transition-transform group-hover:translate-x-1" />
        </button>
      </div>

      {/* Bento Grid Stats */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-6" id="dashboard-stats-grid">
        <div className="col-span-1 md:col-span-2 bg-white p-6 rounded-2xl border border-outline-variant flex flex-col justify-between shadow-sm hover:shadow-md transition-shadow" id="card-stat-total">
          <div className="flex justify-between items-start">
            <div>
              <p className="text-outline font-semibold text-label-sm uppercase tracking-wider">전체 문서</p>
              <h3 className="text-4xl font-extrabold text-primary mt-2">{totalCount.toLocaleString()} <span className="text-sm font-normal text-outline">건</span></h3>
            </div>
            <div className="p-3 bg-secondary/10 rounded-xl text-secondary">
              <Sparkles className="w-6 h-6 fill-secondary/20" />
            </div>
          </div>
          <div className="mt-6 flex items-center gap-2">
            <span className="text-secondary font-bold flex items-center text-sm bg-secondary/5 px-2 py-0.5 rounded-full">
              <TrendingUp className="w-4 h-4 inline mr-1" /> {processingCount}
            </span>
            <span className="text-outline text-body-sm">건 처리 중</span>
          </div>
        </div>

        <div className="bg-white p-6 rounded-2xl border border-outline-variant flex flex-col justify-between shadow-sm hover:shadow-md transition-shadow" id="card-stat-top-folder">
          <div className="flex items-center gap-3 mb-4">
            <span className="p-2 bg-primary/5 text-primary rounded-lg">
              <Folder className="w-5 h-5" />
            </span>
            <span className="font-semibold text-label-md text-on-surface truncate">{topFolder.name}</span>
          </div>
          <div>
            <p className="text-3xl font-extrabold text-on-surface">{topFolder.count}<span className="text-sm font-normal text-outline ml-1">건</span></p>
            <div className="mt-4 w-full bg-surface-container rounded-full h-1.5 overflow-hidden">
              <div className="bg-primary h-full rounded-full transition-all duration-500" style={{ width: `${topPercent}%` }}></div>
            </div>
          </div>
        </div>

        <div className="bg-white p-6 rounded-2xl border border-outline-variant flex flex-col justify-between shadow-sm hover:shadow-md transition-shadow" id="card-stat-starred">
          <div className="flex items-center gap-3 mb-4">
            <span className="p-2 bg-secondary/5 text-secondary rounded-lg">
              <FileText className="w-5 h-5" />
            </span>
            <span className="font-semibold text-label-md text-on-surface">중요 문서</span>
          </div>
          <div>
            <p className="text-3xl font-extrabold text-on-surface">{starredCount}<span className="text-sm font-normal text-outline ml-1">건</span></p>
            <div className="mt-4 w-full bg-surface-container rounded-full h-1.5 overflow-hidden">
              <div className="bg-secondary h-full rounded-full transition-all duration-500" style={{ width: `${totalCount > 0 ? Math.round((starredCount / totalCount) * 100) : 0}%` }}></div>
            </div>
          </div>
        </div>
      </div>

      {/* AI Recommendations: Glassmorphism Cards */}
      <section id="dashboard-ai-recommendations-section">
        <h2 className="text-xl font-bold text-on-surface mb-6 flex items-center gap-2">
          <Sparkles className="w-5 h-5 text-secondary fill-secondary/20" />
          AI 추천 스마트 작업
        </h2>
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6" id="ai-recomm-grid">
          {/* Card 1: Summarize */}
          <div className="bg-white/80 backdrop-blur-md p-6 rounded-3xl border-l-4 border-l-secondary border border-outline-variant/50 flex flex-col hover:scale-[1.01] transition-all shadow-sm hover:shadow-md relative overflow-hidden group" id="recomm-card-summarize">
            <div className="absolute right-2 top-2 w-16 h-16 bg-secondary/5 blur-xl rounded-full"></div>
            <div className="flex items-center gap-2 text-secondary mb-3">
              <Sparkles className="w-4 h-4 fill-secondary/20" />
              <span className="font-bold text-xs uppercase tracking-wider">요약 생성 필요</span>
            </div>
            <h4 className="font-bold text-body-lg text-on-surface mb-2 group-hover:text-secondary transition-colors">2024 하반기 경영 전략.pdf</h4>
            <p className="text-body-sm text-on-surface-variant flex-1 leading-relaxed">최근 업로드된 핵심 조직 전략 문서입니다. AI 가독성을 높이기 위해 3줄 핵심 요약을 생성할 준비가 되었습니다.</p>
            
            <button 
              disabled={completedActions.includes("summarize") || loadingAction === "summarize"}
              onClick={() => handleRunSummary("summarize")}
              className={`mt-4 py-2.5 px-4 rounded-xl font-semibold text-label-md text-center transition-all cursor-pointer w-full ${
                completedActions.includes("summarize") 
                  ? "bg-emerald-100 text-emerald-700 border border-emerald-200"
                  : loadingAction === "summarize"
                  ? "bg-secondary/20 text-secondary cursor-wait animate-pulse"
                  : "bg-secondary text-white hover:bg-opacity-95 shadow-md shadow-secondary/15"
              }`}
              id="btn-run-summary-recomm"
            >
              {completedActions.includes("summarize") ? (
                <span className="flex items-center justify-center gap-1"><Check className="w-4 h-4" /> 완료됨</span>
              ) : loadingAction === "summarize" ? (
                "AI 요약 처리 중..."
              ) : (
                "요약 생성하기"
              )}
            </button>
          </div>

          {/* Card 2: Auto Highlight */}
          <div className="bg-white/80 backdrop-blur-md p-6 rounded-3xl border-l-4 border-l-amber-500 border border-outline-variant/50 flex flex-col hover:scale-[1.01] transition-all shadow-sm hover:shadow-md relative overflow-hidden group" id="recomm-card-highlight">
            <div className="absolute right-2 top-2 w-16 h-16 bg-amber-500/5 blur-xl rounded-full"></div>
            <div className="flex items-center gap-2 text-amber-600 mb-3">
              <Highlighter className="w-4 h-4" />
              <span className="font-bold text-xs uppercase tracking-wider">핵심 내용 하이라이트</span>
            </div>
            <h4 className="font-bold text-body-lg text-on-surface mb-2 group-hover:text-amber-600 transition-colors">5월 예산 집행 현황.xlsx</h4>
            <p className="text-body-sm text-on-surface-variant flex-1 leading-relaxed">예산 초과/부족 등 핵심 수치가 담긴 문서입니다. AI가 중요 항목을 자동으로 하이라이트할 준비가 되었습니다.</p>

            <button
              disabled={completedActions.includes("highlight") || loadingAction === "highlight"}
              onClick={() => handleApplyHighlight("highlight")}
              className={`mt-4 py-2.5 px-4 rounded-xl font-semibold text-label-md text-center transition-all cursor-pointer w-full ${
                completedActions.includes("highlight")
                  ? "bg-emerald-100 text-emerald-700 border border-emerald-200"
                  : loadingAction === "highlight"
                  ? "bg-amber-500/20 text-amber-600 cursor-wait animate-pulse"
                  : "bg-amber-500 text-white hover:bg-opacity-95 shadow-md shadow-amber-500/15"
              }`}
              id="btn-run-highlight-recomm"
            >
              {completedActions.includes("highlight") ? (
                <span className="flex items-center justify-center gap-1"><Check className="w-4 h-4" /> 하이라이트 완료</span>
              ) : loadingAction === "highlight" ? (
                "하이라이트 처리 중..."
              ) : (
                "자동 하이라이트 적용"
              )}
            </button>
          </div>

          {/* Card 3: Merge Suggestion */}
          <div className="bg-white/80 backdrop-blur-md p-6 rounded-3xl border-l-4 border-l-purple-600 border border-outline-variant/50 flex flex-col hover:scale-[1.01] transition-all shadow-sm hover:shadow-md relative overflow-hidden group" id="recomm-card-merge">
            <div className="absolute right-2 top-2 w-16 h-16 bg-purple-100/50 blur-xl rounded-full"></div>
            <div className="flex items-center gap-2 text-purple-600 mb-3">
              <Merge className="w-4 h-4" />
              <span className="font-bold text-xs uppercase tracking-wider">문서 통합 제안</span>
            </div>
            <h4 className="font-bold text-body-lg text-on-surface mb-2 group-hover:text-purple-600 transition-colors">프로젝트 알파 관련 중복 문서</h4>
            <p className="text-body-sm text-on-surface-variant flex-1 leading-relaxed">최근 생성된 '프로젝트 알파' 협력 및 회의 메모 3건이 발견되었습니다. AI 기술로 중복을 없애고 하나로 병합해 보세요.</p>
            
            <button 
              disabled={completedActions.includes("merge") || loadingAction === "merge"}
              onClick={() => handleMergeDocs("merge")}
              className={`mt-4 py-2.5 px-4 rounded-xl font-semibold text-label-md text-center transition-all cursor-pointer w-full ${
                completedActions.includes("merge") 
                  ? "bg-emerald-100 text-emerald-700 border border-emerald-200"
                  : loadingAction === "merge"
                  ? "bg-purple-100 text-purple-700 cursor-wait animate-pulse"
                  : "bg-purple-600 text-white hover:bg-opacity-95 shadow-md shadow-purple-600/15"
              }`}
              id="btn-run-merge-recomm"
            >
              {completedActions.includes("merge") ? (
                <span className="flex items-center justify-center gap-1"><Check className="w-4 h-4" /> 기획서 병합 완료</span>
              ) : loadingAction === "merge" ? (
                "AI 병합 및 중복 정리 중..."
              ) : (
                "통합 제안 처리"
              )}
            </button>
          </div>
        </div>
      </section>

      {/* Recently Accessed Docs Table */}
      <section className="bg-white rounded-3xl border border-outline-variant overflow-hidden shadow-sm" id="recent-documents-dashboard-section">
        <div className="px-8 py-5 border-b border-outline-variant flex justify-between items-center" id="recent-docs-header-bar">
          <h2 className="text-lg font-bold text-on-surface">최근 접근 및 가공한 문서</h2>
          <button 
            onClick={() => onNavigateToTab("documents")}
            className="p-2 hover:bg-surface-container rounded-lg transition-colors cursor-pointer text-outline hover:text-on-surface"
            id="btn-grid-view-tab"
            title="문서 보관함 가기"
          >
            <ArrowRight className="w-5 h-5" />
          </button>
        </div>
        <div className="overflow-x-auto" id="recent-docs-table-wrapper">
          <table className="w-full text-left" id="recent-docs-table">
            <thead>
              <tr className="bg-surface-container-low text-outline text-label-sm font-semibold border-b border-outline-variant uppercase tracking-wider" id="recent-docs-table-head">
                <th className="px-8 py-4">문서 이름</th>
                <th className="px-8 py-4">AI 추출 태그</th>
                <th className="px-8 py-4">마지막 수정</th>
                <th className="px-8 py-4">보안 등급</th>
                <th className="px-8 py-4 text-center">RAG 즉시 물어보기</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-outline-variant" id="recent-docs-table-body">
              {documents.slice(0, 4).map((doc) => (
                <tr key={doc.id} className="hover:bg-surface-container-low transition-colors group" id={`recent-row-${doc.id}`}>
                  <td className="px-8 py-5">
                    <div className="flex items-center gap-3">
                      {doc.fileType === "pdf" ? (
                        <FileText className="w-6 h-6 text-rose-500 shrink-0" />
                      ) : doc.fileType === "xlsx" ? (
                        <FileSpreadsheet className="w-6 h-6 text-emerald-500 shrink-0" />
                      ) : (
                        <FileText className="w-6 h-6 text-blue-500 shrink-0" />
                      )}
                      <div>
                        <p className="font-semibold text-label-md text-on-surface leading-tight">{doc.name}</p>
                        <p className="text-xs text-outline mt-0.5">{formatBytes(doc.sizeBytes)}</p>
                      </div>
                    </div>
                  </td>
                  <td className="px-8 py-5">
                    <div className="flex flex-wrap gap-1.5">
                      {doc.tags.map((tag, idx) => (
                        <span 
                          key={idx} 
                          className="px-2.5 py-0.5 bg-primary/5 text-primary text-[11px] font-semibold rounded-full border border-primary/10"
                        >
                          #{tag}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="px-8 py-5 text-body-sm text-on-surface-variant">
                    {formatDateTime(doc.modifiedAt)}
                  </td>
                  <td className="px-8 py-5">
                    <span className={`inline-flex items-center gap-1 text-xs font-bold ${
                      doc.securityRank === "기밀" ? "text-error" : "text-secondary"
                    }`}>
                      <span className={`w-1.5 h-1.5 rounded-full ${doc.securityRank === "기밀" ? "bg-error" : "bg-secondary"}`}></span>
                      {doc.securityRank}
                    </span>
                  </td>
                  <td className="px-8 py-5 text-center">
                    <button 
                      onClick={() => onNavigateToChat([doc.id])}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-secondary text-white text-xs font-semibold rounded-xl hover:bg-opacity-90 shadow-sm transition-all cursor-pointer"
                      id={`btn-rag-ask-${doc.id}`}
                    >
                      <Sparkles className="w-3.5 h-3.5 fill-white/20" />
                      RAG 검색 및 대화
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </motion.div>
  );
}
