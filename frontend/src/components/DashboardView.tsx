import { useState, useEffect } from "react";
import { motion } from "motion/react";
import {
  Files,
  Sparkles,
  ArrowRight,
  Folder,
  FileText,
  LoaderCircle,
  Star,
  FileSpreadsheet,
  Presentation
} from "lucide-react";
import type { Document, Folder as FolderType } from "../types";
import { formatBytes } from "../utils/formatBytes";
import { formatDateTime } from "../utils/formatDateTime";
import { isDescendantOrSelf } from "../utils/folderTree";
import { listFolders } from "../api/folders";

interface DashboardViewProps {
  documents: Document[];
  onNavigateToChat: (docIds: string[]) => void;
  onNavigateToTab: (tab: string) => void;
}

export default function DashboardView({ documents, onNavigateToChat, onNavigateToTab }: DashboardViewProps) {
  const [folders, setFolders] = useState<FolderType[]>([]);
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
  const topFolderDescription = topFolder.name === "미분류" ? "폴더가 지정되지 않은 문서" : "문서가 가장 많은 폴더";

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
          내 문서 보관함으로 이동
          <ArrowRight className="w-4 h-4 transition-transform group-hover:translate-x-1" />
        </button>
      </div>

      {/* Bento Grid Stats */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-6" id="dashboard-stats-grid">
        <div className="col-span-1 md:col-span-2 bg-white p-6 rounded-2xl border border-outline-variant flex flex-col justify-between shadow-sm hover:shadow-md transition-shadow" id="card-stat-total">
          <div className="flex items-start gap-3">
            <div className="p-3 bg-primary/10 rounded-xl text-primary">
              <Files className="w-6 h-6" />
            </div>
            <div>
              <p className="text-outline font-semibold text-label-sm uppercase tracking-wider">전체 문서</p>
              <h3 className="text-4xl font-extrabold text-primary mt-2">{totalCount.toLocaleString()} <span className="text-sm font-normal text-outline">개</span></h3>
            </div>
          </div>
          <div className="mt-6 flex items-center gap-2">
            <span className="text-secondary font-bold flex items-center text-sm bg-secondary/5 px-2 py-0.5 rounded-full">
              <LoaderCircle className="w-4 h-4 inline mr-1" /> {processingCount}개
            </span>
            <span className="text-outline text-body-sm">업로드·분석 처리 중인 문서</span>
          </div>
        </div>

        <div className="bg-white p-6 rounded-2xl border border-outline-variant flex flex-col justify-between shadow-sm hover:shadow-md transition-shadow" id="card-stat-top-folder">
          <div className="flex items-center gap-3 mb-4">
            <span className="p-2 bg-primary/5 text-primary rounded-lg">
              <Folder className="w-5 h-5" />
            </span>
            <div className="min-w-0">
              <span className="font-semibold text-label-md text-on-surface truncate block">{topFolder.name}</span>
              <span className="text-[10px] text-outline">{topFolderDescription}</span>
            </div>
          </div>
          <div>
            <p className="text-3xl font-extrabold text-on-surface">{topFolder.count}<span className="text-sm font-normal text-outline ml-1">개</span></p>
            <div className="mt-4 w-full bg-surface-container rounded-full h-1.5 overflow-hidden">
              <div className="bg-primary h-full rounded-full transition-all duration-500" style={{ width: `${topPercent}%` }}></div>
            </div>
          </div>
        </div>

        <div className="bg-white p-6 rounded-2xl border border-outline-variant flex flex-col justify-between shadow-sm hover:shadow-md transition-shadow" id="card-stat-starred">
          <div className="flex items-center gap-3 mb-4">
            <span className="p-2 bg-secondary/5 text-secondary rounded-lg">
              <Star className="w-5 h-5" />
            </span>
            <div>
              <span className="font-semibold text-label-md text-on-surface block">중요 문서</span>
              <span className="text-[10px] text-outline">별표 표시한 문서</span>
            </div>
          </div>
          <div>
            <p className="text-3xl font-extrabold text-on-surface">{starredCount}<span className="text-sm font-normal text-outline ml-1">개</span></p>
            <div className="mt-4 w-full bg-surface-container rounded-full h-1.5 overflow-hidden">
              <div className="bg-secondary h-full rounded-full transition-all duration-500" style={{ width: `${totalCount > 0 ? Math.round((starredCount / totalCount) * 100) : 0}%` }}></div>
            </div>
          </div>
        </div>
      </div>

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
                <th className="px-8 py-4 text-center">RAG 즉시 물어보기</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-outline-variant" id="recent-docs-table-body">
              {documents.length === 0 && (
                <tr id="recent-docs-empty-row">
                  <td colSpan={4} className="px-8 py-24 text-center text-body-sm text-outline">
                    아직 업로드한 문서가 없습니다. 문서를 업로드하면 여기에 표시됩니다.
                  </td>
                </tr>
              )}
              {documents.slice(0, 12).map((doc) => (
                <tr key={doc.id} className="hover:bg-surface-container-low transition-colors group" id={`recent-row-${doc.id}`}>
                  <td className="px-8 py-5">
                    <div className="flex items-center gap-3">
                      {doc.fileType === "pdf" ? (
                        <FileText className="w-6 h-6 text-rose-500 shrink-0" />
                      ) : doc.fileType === "xlsx" ? (
                        <FileSpreadsheet className="w-6 h-6 text-emerald-500 shrink-0" />
                      ) : doc.fileType === "pptx" ? (
                        <Presentation className="w-6 h-6 text-orange-500 shrink-0" />
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
