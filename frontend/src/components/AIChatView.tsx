import React, { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  Sparkles,
  Send,
  Mic,
  Paperclip,
  User,
  FileText,
  FileSpreadsheet,
  PlusCircle,
  Link2,
  Check,
  X,
  Plus,
  Pencil,
} from "lucide-react";
import type { Document, ChatSession, ChatMessage, Folder } from "../types";
import { getFolderPath } from "../utils/folderTree";
import { listFolders } from "../api/folders";

interface AIChatViewProps {
  documents: Document[];
  chatSessions: ChatSession[];
  activeChatSessionId: string;
  onSelectChatSession: (id: string) => void;
  onNewChatTab: () => void;
  onCloseChatTab: (id: string) => void;
  onRenameChatTab: (id: string, title: string) => void;
  onToggleDocSelection: (id: string) => void;
  onSendMessage: (text: string, refDocIds: string[]) => Promise<void>;
  isLoadingChat: boolean;
}

export default function AIChatView({
  documents,
  chatSessions,
  activeChatSessionId,
  onSelectChatSession,
  onNewChatTab,
  onCloseChatTab,
  onRenameChatTab,
  onToggleDocSelection,
  onSendMessage,
  isLoadingChat
}: AIChatViewProps) {
  const [inputText, setInputText] = useState("");
  const [isAddSourceOpen, setIsAddSourceOpen] = useState(false);
  const [folders, setFolders] = useState<Folder[]>([]);
  useEffect(() => {
    listFolders().then(setFolders).catch(() => {});
  }, []);

  const activeSession = chatSessions.find((s) => s.id === activeChatSessionId) ?? chatSessions[0];
  const selectedDocIds = activeSession.selectedDocIds;
  const chatHistory = activeSession.chatHistory;

  const chatEndRef = useRef<HTMLDivElement>(null);

  // Auto scroll to bottom on new messages
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatHistory, isLoadingChat]);

  const handleSend = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!inputText.trim() || isLoadingChat) return;

    const textToSend = inputText;
    setInputText("");
    await onSendMessage(textToSend, selectedDocIds);
  };

  const handleSuggestionClick = async (suggestion: string) => {
    if (isLoadingChat) return;
    await onSendMessage(suggestion, selectedDocIds);
  };

  const suggestions = [
    "가장 최근의 지출 요약해줘",
    "올해 핵심 리스크는 뭐야?",
    "전략 기획안에서 인력 충원 계획은?"
  ];

  return (
    <motion.div 
      initial={{ opacity: 0, y: 15 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -15 }}
      className="flex flex-row h-[calc(100vh-140px)] border border-outline-variant rounded-3xl overflow-hidden bg-white shadow-sm"
      id="ai-chat-view-container"
    >
      {/* Left Pane: Document Reference (RAG Sources) */}
      <aside className="w-80 border-r border-outline-variant bg-surface-container-low/35 flex flex-col overflow-hidden" id="chat-sources-panel">
        <div className="p-6 border-b border-outline-variant bg-white" id="sources-header">
          <h2 className="text-xl font-bold text-on-surface">참조 문서</h2>
          <p className="text-xs text-outline mt-1 font-semibold">
            현재 AI 가독 대조 중인 {selectedDocIds.length}개의 활성 소스
          </p>
        </div>

        {/* References List */}
        <div className="flex-1 overflow-y-auto p-4 space-y-3 custom-scrollbar" id="sources-list-container">
          {documents.map((doc) => {
            const isChecked = selectedDocIds.includes(doc.id);
            return (
              <div 
                key={doc.id}
                onClick={() => onToggleDocSelection(doc.id)}
                className={`p-3 bg-white border rounded-xl hover:border-secondary transition-all cursor-pointer relative group ${
                  isChecked ? "border-secondary/70 shadow-sm ring-1 ring-secondary/10" : "border-outline-variant"
                }`}
                id={`source-item-${doc.id}`}
              >
                <div className="flex items-start gap-3">
                  {doc.fileType === "pdf" ? (
                    <FileText className="w-9 h-9 text-rose-500 bg-rose-50 p-2 rounded-lg shrink-0" />
                  ) : doc.fileType === "xlsx" ? (
                    <FileSpreadsheet className="w-9 h-9 text-emerald-500 bg-emerald-50 p-2 rounded-lg shrink-0" />
                  ) : (
                    <FileText className="w-9 h-9 text-blue-500 bg-blue-50 p-2 rounded-lg shrink-0" />
                  )}
                  <div className="flex-1 min-w-0">
                    <p className="text-body-sm font-bold text-on-surface truncate leading-tight">{doc.name}</p>
                    <p className="text-[11px] text-outline mt-1 font-sans">{getFolderPath(doc.folderId, folders)}</p>
                  </div>

                  {/* Active Indicator Checkbox */}
                  <div className={`w-4 h-4 rounded-full border flex items-center justify-center shrink-0 mt-0.5 ${
                    isChecked ? "bg-secondary border-secondary text-white" : "border-outline-variant"
                  }`}>
                    {isChecked && <Check className="w-2.5 h-2.5 stroke-[3]" />}
                  </div>
                </div>

                {isChecked && (
                  <div className="mt-2.5 flex items-center gap-1">
                    <span className="w-1.5 h-1.5 rounded-full bg-secondary animate-pulse"></span>
                    <span className="text-[10px] text-secondary font-bold">인용 가능한 컨텍스트 포함됨</span>
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {/* Add Source Quick Button */}
        <div className="p-4 bg-white border-t border-outline-variant" id="sources-footer">
          <button 
            onClick={() => setIsAddSourceOpen(true)}
            className="w-full py-2.5 flex items-center justify-center gap-2 text-primary font-bold text-label-md hover:bg-primary/5 rounded-xl border border-primary/20 transition-colors cursor-pointer"
            id="btn-trigger-add-sources"
          >
            <PlusCircle className="w-4 h-4" /> 소스 신규 업로드
          </button>
        </div>
      </aside>

      {/* Right Pane: Active RAG Chat Interface */}
      <section className="flex-1 flex flex-col relative bg-white overflow-hidden" id="chat-workspace-pane">
        {/* Chat Tabs Bar: 여러 개의 독립된 대화 창을 탭으로 전환 */}
        <div
          className="flex items-end gap-1 px-3 pt-2.5 border-b border-outline-variant bg-surface-container-low/40 overflow-x-auto no-scrollbar shrink-0"
          id="chat-tabs-bar"
        >
          {chatSessions.map((session) => (
            <ChatTab
              key={session.id}
              session={session}
              isActive={session.id === activeChatSessionId}
              closable={chatSessions.length > 1}
              onSelect={() => onSelectChatSession(session.id)}
              onClose={() => onCloseChatTab(session.id)}
              onRename={(title) => onRenameChatTab(session.id, title)}
            />
          ))}
          <button
            type="button"
            onClick={onNewChatTab}
            className="p-2 mb-1.5 ml-1 text-outline hover:text-primary hover:bg-white rounded-lg transition-colors cursor-pointer shrink-0"
            title="새 대화 탭 열기"
            id="btn-new-chat-tab"
          >
            <Plus className="w-4 h-4" />
          </button>
        </div>

        {/* Messages Feed */}
        <div className="flex-1 overflow-y-auto p-8 space-y-8 pb-36 custom-scrollbar" id="messages-scroller">
          {chatHistory.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center text-center p-8" id="chat-welcome-state">
              <div className="w-16 h-16 bg-secondary/10 text-secondary rounded-2xl flex items-center justify-center mb-4">
                <Sparkles className="w-8 h-8 fill-secondary/10" />
              </div>
              <h3 className="text-2xl font-bold text-on-surface">지능형 RAG 문서 검색</h3>
              <p className="text-body-md text-on-surface-variant max-w-md mt-2 leading-relaxed">
                왼쪽에서 분석을 원하시는 임의의 핵심 문서 소스들을 선택하신 후 자유롭게 대조 질문해 보세요. 
                AI가 문서를 정밀 탐색하여 정확한 사실과 인용 출처를 밝힙니다.
              </p>
            </div>
          ) : (
            <>
              {/* Date Separation Badge */}
              <div className="flex justify-center" id="date-separation">
                <span className="px-4 py-1 bg-surface-container text-outline text-[11px] rounded-full font-bold uppercase tracking-widest">
                  오늘 실시간 분석 대화
                </span>
              </div>

              {chatHistory.map((msg) => (
                <div 
                  key={msg.id}
                  className={`flex items-start gap-4 max-w-4xl ${
                    msg.sender === "user" ? "justify-end ml-auto" : "justify-start mr-auto"
                  }`}
                  id={`chat-msg-row-${msg.id}`}
                >
                  {/* Left Avatar for AI */}
                  {msg.sender === "ai" && (
                    <div className="w-10 h-10 rounded-xl bg-primary-container flex items-center justify-center shadow-md shadow-primary/10 shrink-0">
                      <Sparkles className="w-5 h-5 text-white fill-white/10" />
                    </div>
                  )}

                  {/* Message Bubble Column */}
                  <div className={`flex flex-col ${msg.sender === "user" ? "items-end" : "items-start"}`}>
                    <div className={`p-5 rounded-2xl shadow-sm leading-relaxed ${
                      msg.sender === "user" 
                        ? "bg-surface-variant text-on-surface rounded-tr-none font-sans text-body-md" 
                        : "bg-white border border-outline-variant/60 rounded-tl-none border-l-4 border-l-secondary relative"
                    }`} id={`chat-msg-bubble-${msg.id}`}>
                      
                      {msg.sender === "ai" && (
                        <div className="flex items-center gap-2 mb-3 border-b border-outline-variant/30 pb-2">
                          <span className="text-label-md text-primary font-bold">AI Drive Assistant</span>
                          <span className="px-2 py-0.5 bg-secondary/10 text-secondary text-[10px] font-extrabold rounded-full animate-pulse">RAG 분석 완료</span>
                        </div>
                      )}

                      {msg.sender === "ai" && (msg.routing || msg.mapResults || msg.modelUsed) && (
                        <AIChatProcessDetails msg={msg} />
                      )}

                      {/* Message Content Body */}
                      <div className="text-body-md text-on-surface leading-relaxed space-y-3 font-sans whitespace-pre-line">
                        {msg.text}
                      </div>

                      {/* Citation / Sources link in AI bubbles */}
                      {msg.sender === "ai" && msg.citations.length > 0 && (
                        <div className="mt-5 flex flex-wrap gap-2 pt-4 border-t border-outline-variant/30">
                          <span className="text-[11px] text-outline w-full mb-1 font-bold">인용된 신뢰 근거 문서:</span>
                          {msg.citations.map((cite, idx) => (
                            <button 
                              key={idx}
                              onClick={() => {
                                alert(`[인용 근거 확인]\n폴더: ${cite.info}\n파일명: ${cite.name}\n\n해당 파일은 검증된 사실만을 담고 있는 보안 저장소에 보관되어 있습니다.`);
                              }}
                              className="px-3 py-1 bg-surface-container text-secondary text-xs font-bold rounded-lg border border-secondary/10 hover:bg-secondary/15 transition-all flex items-center gap-1 cursor-pointer"
                            >
                              <Link2 className="w-3.5 h-3.5" />
                              {cite.name.split(".")[0]}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                    {/* Timestamp and Process Time */}
                    <span className="text-[10px] text-outline mt-2 font-medium" id={`msg-meta-${msg.id}`}>
                      {msg.timestamp} {msg.processingTime && `· AI 분석처리 ${msg.processingTime}`}
                    </span>
                  </div>

                  {/* Right Avatar for User */}
                  {msg.sender === "user" && (
                    <div className="w-8 h-8 rounded-full bg-secondary-container flex items-center justify-center text-on-secondary-container font-extrabold text-xs shrink-0 mt-1">
                      <User className="w-4 h-4 text-on-secondary-container" />
                    </div>
                  )}
                </div>
              ))}

              {/* Chat loading states */}
              {isLoadingChat && (
                <div className="flex justify-start items-start gap-4 max-w-4xl mr-auto animate-pulse">
                  <div className="w-10 h-10 rounded-xl bg-secondary/15 flex items-center justify-center shrink-0">
                    <Sparkles className="w-5 h-5 text-secondary animate-spin" />
                  </div>
                  <div className="flex flex-col items-start">
                    <div className="p-5 bg-white border border-outline-variant/60 rounded-2xl rounded-tl-none border-l-4 border-l-secondary">
                      <div className="flex items-center gap-2 mb-2">
                        <span className="text-label-md text-primary font-bold">AI Drive Assistant</span>
                        <span className="px-2 py-0.5 bg-secondary/10 text-secondary text-[10px] font-bold rounded-full">지능형 텍스트 검독 중...</span>
                      </div>
                      <div className="flex items-center gap-1.5 mt-2 py-1">
                        <span className="w-2.5 h-2.5 bg-secondary rounded-full animate-bounce" style={{ animationDelay: "0ms" }}></span>
                        <span className="w-2.5 h-2.5 bg-secondary rounded-full animate-bounce" style={{ animationDelay: "150ms" }}></span>
                        <span className="w-2.5 h-2.5 bg-secondary rounded-full animate-bounce" style={{ animationDelay: "300ms" }}></span>
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </>
          )}
          <div ref={chatEndRef} />
        </div>

        {/* Floating Input area at bottom */}
        <div className="absolute bottom-0 left-0 right-0 p-6 bg-gradient-to-t from-white via-white/95 to-transparent border-t border-outline-variant/20" id="chat-input-bar-container">
          <div className="max-w-4xl mx-auto">
            <form onSubmit={handleSend} className="relative bg-white/90 border border-outline-variant rounded-2xl shadow-xl overflow-hidden focus-within:ring-2 focus-within:ring-primary focus-within:border-primary transition-all">
              <div className="flex items-end gap-2 p-4">
                <button 
                  type="button"
                  onClick={() => alert("현재 베타 버전에서는 임의의 새 텍스트 파일 업로드 또는 사내 위키 템플릿 로드를 지원합니다. 왼쪽 '새 문서 추가' 창을 이용해 주세요.")}
                  className="p-2.5 text-outline hover:text-primary transition-colors cursor-pointer"
                  title="파일 첨부"
                >
                  <Paperclip className="w-5 h-5" />
                </button>
                
                <textarea 
                  value={inputText}
                  onChange={(e) => setInputText(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      handleSend();
                    }
                  }}
                  placeholder={
                    selectedDocIds.length > 0 
                      ? "AI에게 분석중인 문서들에 관해 무엇이든 질문해 보세요..." 
                      : "질문을 전송하려면 왼쪽에서 1개 이상의 참조 문서를 선택해 주세요..."
                  }
                  rows={1}
                  className="flex-1 bg-transparent border-none focus:ring-0 resize-none py-2 text-body-md outline-none min-h-[40px] max-h-48 font-sans"
                  style={{ height: "auto" }}
                />

                <div className="flex items-center gap-2 mb-1">
                  <button 
                    type="button" 
                    onClick={() => alert("사내 AI Drive 전용 음성 보이스 제어 모듈이 동작을 준비 중입니다.")}
                    className="p-2.5 text-outline hover:text-secondary transition-colors cursor-pointer"
                    title="음성 입력"
                  >
                    <Mic className="w-5 h-5" />
                  </button>
                  <button 
                    type="submit"
                    disabled={isLoadingChat || !inputText.trim() || selectedDocIds.length === 0}
                    className="w-10 h-10 bg-primary disabled:bg-primary/40 text-white rounded-xl flex items-center justify-center shadow-lg shadow-primary/20 hover:bg-opacity-95 transition-all cursor-pointer"
                    id="btn-send-chat-msg"
                  >
                    <Send className="w-4 h-4" />
                  </button>
                </div>
              </div>

              {/* Suggestions chips */}
              <div className="px-4 pb-4 flex gap-2 overflow-x-auto no-scrollbar scroll-smooth" id="suggestions-row">
                {suggestions.map((suggestion, idx) => (
                  <button 
                    key={idx}
                    type="button"
                    disabled={isLoadingChat || selectedDocIds.length === 0}
                    onClick={() => handleSuggestionClick(suggestion)}
                    className="px-3 py-1.5 bg-surface-container-low border border-outline-variant rounded-full text-xs text-on-surface-variant hover:bg-surface-variant transition-colors whitespace-nowrap cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    "{suggestion}"
                  </button>
                ))}
              </div>
            </form>
            <p className="text-center text-[10px] text-outline mt-3 font-medium">
              AI Drive는 제공된 소스 문서를 완전 분석하여 대답합니다. 민감한 기밀 사항은 안전 보안 필터링에 의해 이메일 보고용으로 가려질 수 있습니다.
            </p>
          </div>
        </div>
      </section>

      {/* Popover overlay modal trigger for Add Sources */}
      <AnimatePresence>
        {isAddSourceOpen && (
          <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm">
            <motion.div 
              initial={{ scale: 0.95, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.95, opacity: 0 }}
              className="bg-white rounded-3xl w-full max-w-md p-6 overflow-hidden shadow-2xl border border-outline-variant"
            >
              <div className="flex justify-between items-center mb-4">
                <h3 className="text-lg font-bold text-on-surface flex items-center gap-1.5">
                  <Sparkles className="w-5 h-5 text-secondary fill-secondary/10" />
                  RAG 참조 소스 일괄 설정
                </h3>
                <button 
                  onClick={() => setIsAddSourceOpen(false)}
                  className="p-1.5 hover:bg-surface-container rounded-full text-outline"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>

              <p className="text-xs text-outline mb-4">Ingest하고 싶은 기업 문서들을 토글하여 AI 실시간 지식 베이스(RAG)에 탑재하세요.</p>
              
              <div className="space-y-2 max-h-60 overflow-y-auto pr-1">
                {documents.map((doc) => {
                  const isChecked = selectedDocIds.includes(doc.id);
                  return (
                    <div 
                      key={doc.id}
                      onClick={() => onToggleDocSelection(doc.id)}
                      className="flex items-center justify-between p-3 border border-outline-variant hover:bg-surface-container-low rounded-xl cursor-pointer"
                    >
                      <div className="flex items-center gap-3">
                        {doc.fileType === "pdf" ? <FileText className="w-4 h-4 text-rose-500" /> : <FileText className="w-4 h-4 text-blue-500" />}
                        <span className="text-body-sm font-semibold text-on-surface line-clamp-1">{doc.name}</span>
                      </div>
                      <div className={`w-5 h-5 rounded border flex items-center justify-center ${
                        isChecked ? "bg-secondary border-secondary text-white" : "border-outline-variant"
                      }`}>
                        {isChecked && <Check className="w-3.5 h-3.5 stroke-[3]" />}
                      </div>
                    </div>
                  );
                })}
              </div>

              <div className="mt-6 flex justify-end">
                <button 
                  onClick={() => setIsAddSourceOpen(false)}
                  className="px-5 py-2.5 bg-primary text-white text-xs font-bold rounded-xl hover:bg-opacity-95 shadow-md shadow-primary/15 cursor-pointer"
                >
                  지식 소스 설정 완료
                </button>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

function ChatTab({
  session,
  isActive,
  closable,
  onSelect,
  onClose,
  onRename,
}: {
  session: ChatSession;
  isActive: boolean;
  closable: boolean;
  onSelect: () => void;
  onClose: () => void;
  onRename: (title: string) => void;
}) {
  const [isEditing, setIsEditing] = useState(false);
  const [draftTitle, setDraftTitle] = useState(session.title);

  const commitRename = () => {
    setIsEditing(false);
    const trimmed = draftTitle.trim();
    onRename(trimmed || session.title);
  };

  return (
    <div
      onClick={onSelect}
      className={`group flex items-center gap-2 px-3.5 py-2.5 rounded-t-xl cursor-pointer text-label-md font-semibold whitespace-nowrap border border-b-0 transition-colors ${
        isActive
          ? "bg-white text-primary border-outline-variant"
          : "bg-transparent text-on-surface-variant border-transparent hover:bg-white/60"
      }`}
      id={`chat-tab-${session.id}`}
      title={session.title}
    >
      {isEditing ? (
        <input
          autoFocus
          value={draftTitle}
          onChange={(e) => setDraftTitle(e.target.value)}
          onClick={(e) => e.stopPropagation()}
          onBlur={commitRename}
          onKeyDown={(e) => {
            if (e.key === "Enter") commitRename();
            if (e.key === "Escape") {
              setDraftTitle(session.title);
              setIsEditing(false);
            }
          }}
          className="max-w-[120px] bg-surface-container-low rounded px-1.5 py-0.5 text-label-md font-semibold outline-none ring-1 ring-primary/40"
        />
      ) : (
        <span
          className="max-w-[140px] truncate"
          onDoubleClick={(e) => {
            e.stopPropagation();
            setDraftTitle(session.title);
            setIsEditing(true);
          }}
        >
          {session.title}
        </span>
      )}

      {!isEditing && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            setDraftTitle(session.title);
            setIsEditing(true);
          }}
          className="p-0.5 rounded-full text-outline hover:text-primary hover:bg-primary/5 opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer"
          title="탭 이름 변경"
        >
          <Pencil className="w-3 h-3" />
        </button>
      )}

      {closable && !isEditing && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onClose();
          }}
          className="p-0.5 rounded-full text-outline hover:text-rose-500 hover:bg-rose-50 opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer"
          title="탭 닫기"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      )}
    </div>
  );
}

function AIChatProcessDetails({ msg }: { msg: ChatMessage }) {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <div className="mb-4 bg-surface-container-lowest border border-outline-variant/50 rounded-xl overflow-hidden font-sans w-full">
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="w-full px-3.5 py-2.5 flex items-center justify-between text-[11px] font-extrabold text-primary hover:bg-primary/5 transition-colors cursor-pointer"
      >
        <span className="flex items-center gap-1.5">
          <Sparkles className="w-3.5 h-3.5 animate-pulse text-secondary shrink-0" />
          <span>AI 지능형 RAG 분석 프로세스 {isOpen ? "닫기" : "상세 분석 보기"}</span>
        </span>
        <span className="text-[10px] text-outline font-semibold">
          {msg.routing?.mode === "synthesis" ? "📊 종합 융합 분석 (Map-Reduce)" : "🔍 정밀 사실 매칭 (Lookup)"}
        </span>
      </button>

      {isOpen && (
        <div className="p-3.5 border-t border-outline-variant/25 bg-surface-bright space-y-3.5 text-[11px] leading-relaxed">
          {/* Query Routing */}
          {msg.routing && (
            <div className="space-y-1">
              <div className="flex items-center gap-1.5">
                <span className={`px-1.5 py-0.5 rounded text-[9px] font-extrabold ${
                  msg.routing.mode === "synthesis" 
                    ? "bg-purple-50 text-purple-700 border border-purple-100" 
                    : "bg-cyan-50 text-cyan-700 border border-cyan-100"
                }`}>
                  {msg.routing.mode.toUpperCase()}
                </span>
                <span className="text-on-surface font-extrabold text-[10.5px]">질문 라우팅 및 의도 판별</span>
              </div>
              <p className="text-on-surface-variant font-medium pl-1 text-[10.5px] bg-surface-container-low p-2 rounded-lg">
                💡 {msg.routing.reasoning}
              </p>
            </div>
          )}

          {/* Model Used */}
          {msg.modelUsed && (
            <div className="flex justify-between items-center bg-surface-container-low p-2 rounded-lg text-[10px]">
              <span className="text-outline font-bold">수행 모델:</span>
              <span className="font-mono font-extrabold text-secondary">{msg.modelUsed}</span>
            </div>
          )}

          {/* Map Phase Results */}
          {msg.mapResults && msg.mapResults.length > 0 && (
            <div className="space-y-1.5">
              <span className="text-on-surface font-extrabold text-[10.5px] block">🔍 분할 추출 단계 (Map Phase)</span>
              <div className="space-y-1.5">
                {msg.mapResults.map((mapRes, index) => (
                  <div key={index} className="bg-secondary/[0.02] border border-secondary/10 rounded-lg p-2.5 text-[10px]">
                    <div className="flex items-center justify-between font-bold text-secondary mb-1">
                      <span className="truncate max-w-[180px]">📂 {mapRes.docName}</span>
                      <span className="text-[8.5px] bg-secondary/10 px-1 py-0.5 rounded shrink-0">개별 추출 성공</span>
                    </div>
                    <p className="text-on-surface-variant leading-relaxed">
                      {mapRes.partialSummary}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
