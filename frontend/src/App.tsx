import React, { useState, useEffect } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  Sparkles,
  LayoutDashboard,
  FileText,
  Settings,
  HelpCircle,
  LogOut,
  Search,
  Bell,
  Grid,
  Plus,
  HardDrive,
  ShieldCheck
} from "lucide-react";

// Import custom components
import DashboardView from "./components/DashboardView";
import MyDocumentsView from "./components/MyDocumentsView";
import AIChatView from "./components/AIChatView";
import SettingsView from "./components/SettingsView";
import LandingView from "./components/LandingView";
import AdminView from "./components/AdminView";

// Import types
import type { Document, AISettings, ChatMessage, ChatSession } from "./types";

// mockDocuments: 정적 mock 데이터, 백엔드 API 연동은 별도 이슈에서 처리.
// mockAISettings: 최초 렌더링용 fallback 초기값일 뿐 — 실제 값은 아래 useEffect가
// GET /api/v1/users/me/settings로 즉시 덮어쓴다(로그인 안 한 상태면 실패하고 이 값 유지).
import { mockDocuments, mockAISettings } from "./mocks/mockData";
import { getUserSettings, updateUserSettings } from "./api/userSettings";

function getInitialSelectedDocIds(docs: Document[]): string[] {
  if (docs.length > 2) {
    return [docs[0].id, docs[2].id];
  } else if (docs.length > 0) {
    return [docs[0].id];
  }
  return [];
}

let chatSessionSeq = 0;
function createChatSession(selectedDocIds: string[] = [], title?: string): ChatSession {
  chatSessionSeq += 1;
  return {
    id: `session-${Date.now()}-${chatSessionSeq}`,
    title: title ?? `대화 ${chatSessionSeq}`,
    chatHistory: [],
    selectedDocIds
  };
}

export default function App() {
  const [user, setUser] = useState<{ name: string; email: string; role: string } | null>(() => {
    const saved = localStorage.getItem("aidrive_user");
    return saved ? JSON.parse(saved) : null;
  });
  const [activeTab, setActiveTab] = useState<string>("dashboard");
  const [documents, setDocuments] = useState<Document[]>(mockDocuments);
  const [chatSessions, setChatSessions] = useState<ChatSession[]>(() => [
    createChatSession(getInitialSelectedDocIds(mockDocuments))
  ]);
  const [activeChatSessionId, setActiveChatSessionId] = useState<string>(() => chatSessions[0].id);
  const [committedSettings, setCommittedSettings] = useState<AISettings>(mockAISettings);
  const [isUploadOpen, setIsUploadOpen] = useState(false);
  const [isLoadingChat, setIsLoadingChat] = useState(false);
  const [globalSearch, setGlobalSearch] = useState("");

  // 실제 설정 조회 시도 — 프론트 구글 로그인이 아직 mock이라(LandingView 참고, 실제 OAuth
  // 리다이렉트/토큰 저장 미구현) 지금은 항상 토큰이 없어 401로 실패하는 게 정상이고, 그 경우
  // 위에서 초기화한 mockAISettings를 그대로 유지한다(Folder와 동일 패턴).
  useEffect(() => {
    getUserSettings()
      .then(setCommittedSettings)
      .catch((err) => {
        console.warn("[settings] GET /api/v1/users/me/settings 실패 - mock 데이터 유지(비로그인 상태면 정상):", err);
      });
  }, []);

  // Sync global search into documents tab
  const handleGlobalSearchChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setGlobalSearch(e.target.value);
    if (activeTab !== "documents" && activeTab !== "chat") {
      setActiveTab("documents");
    }
  };

  // Upload document (mock: API 연동 전까지는 로컬 상태에만 반영)
  const handleUploadDocument = async (docData: { name: string; content: string; type: string }) => {
    const newDocument: Document = {
      id: `doc-${Date.now()}`,
      name: docData.name,
      content: docData.content,
      sizeBytes: new Blob([docData.content]).size,
      fileType: docData.type,
      folderId: null, // 미분류(루트)
      tags: [],
      modifiedAt: new Date().toLocaleDateString("ko-KR"),
      ownerName: user?.name || "사용자",
      securityRank: "일반",
      summary: "AI 분류 대기 중인 문서입니다. (mock 데이터, 백엔드 연동 전)",
      piiDetected: false
    };

    setDocuments((prevDocs) => [newDocument, ...prevDocs]);
    setChatSessions((prev) =>
      prev.map((session) =>
        session.id === activeChatSessionId
          ? { ...session, selectedDocIds: [newDocument.id, ...session.selectedDocIds] }
          : session
      )
    );

    alert(`AI 분류 성공! (mock)\n\n• 파일명: ${newDocument.name}\n• 분류된 폴더: [미분류]\n• 보안 조치 등급: [${newDocument.securityRank}]`);
  };

  // Toggle RAG document selection (활성 채팅 탭 기준)
  const handleToggleDocSelection = (id: string) => {
    setChatSessions((prev) =>
      prev.map((session) =>
        session.id === activeChatSessionId
          ? {
              ...session,
              selectedDocIds: session.selectedDocIds.includes(id)
                ? session.selectedDocIds.filter((item) => item !== id)
                : [...session.selectedDocIds, id]
            }
          : session
      )
    );
  };

  // Send message to RAG Chat engine (mock: API 연동 전까지는 안내 메시지로 응답)
  const handleSendMessage = async (text: string) => {
    const targetSessionId = activeChatSessionId;
    const userMessage: ChatMessage = {
      id: `chat-${Date.now()}`,
      sender: "user",
      text,
      citations: [],
      timestamp: new Date().toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })
    };

    setChatSessions((prev) =>
      prev.map((session) =>
        session.id === targetSessionId
          ? { ...session, chatHistory: [...session.chatHistory, userMessage] }
          : session
      )
    );
    setIsLoadingChat(true);

    // mock 응답 (실제 RAG 추론 엔진 연동은 별도 이슈)
    await new Promise((resolve) => setTimeout(resolve, 500));
    const aiMessage: ChatMessage = {
      id: `chat-${Date.now() + 1}`,
      sender: "ai",
      text: "이것은 mock 응답입니다. 실제 AI 추론 엔진 연동은 별도 이슈에서 진행됩니다.",
      citations: [],
      timestamp: new Date().toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })
    };
    setChatSessions((prev) =>
      prev.map((session) =>
        session.id === targetSessionId
          ? { ...session, chatHistory: [...session.chatHistory, aiMessage] }
          : session
      )
    );
    setIsLoadingChat(false);
  };

  // Save Settings — 로컬 상태 먼저 반영(데모 흐름 유지) 후 실제 API 호출 시도.
  // 비로그인 상태면 PATCH가 401로 실패하는 게 정상이며, 그 경우
  // 로컬 상태만 갱신된 채로 남는다(Folder의 create/delete와 동일한 폴백 패턴).
  const handleSaveSettings = async (newSettings: AISettings) => {
    setCommittedSettings(newSettings);
    try {
      await updateUserSettings(newSettings);
    } catch (err) {
      console.warn("[settings] PATCH /api/v1/users/me/settings 실패 - 로컬 상태만 갱신됨(비로그인 상태면 정상):", err);
    }
  };

  // Smart navigation from Dashboard/Documents: 지정 문서로 새 채팅 탭을 열어 이동
  const handleNavigateToChat = (docIds: string[]) => {
    const newSession = createChatSession(docIds);
    setChatSessions((prev) => [...prev, newSession]);
    setActiveChatSessionId(newSession.id);
    setActiveTab("chat");
  };

  // 채팅 탭 관리
  const handleNewChatTab = () => {
    const newSession = createChatSession();
    setChatSessions((prev) => [...prev, newSession]);
    setActiveChatSessionId(newSession.id);
  };

  const handleCloseChatTab = (sessionId: string) => {
    setChatSessions((prev) => {
      const remaining = prev.filter((session) => session.id !== sessionId);
      if (remaining.length === 0) {
        const fresh = createChatSession();
        setActiveChatSessionId(fresh.id);
        return [fresh];
      }
      if (activeChatSessionId === sessionId) {
        setActiveChatSessionId(remaining[remaining.length - 1].id);
      }
      return remaining;
    });
  };

  const handleRenameChatTab = (sessionId: string, title: string) => {
    const trimmed = title.trim();
    if (!trimmed) return;
    setChatSessions((prev) =>
      prev.map((session) => (session.id === sessionId ? { ...session, title: trimmed } : session))
    );
  };

  const handleUploadClickOnSidebar = () => {
    setActiveTab("documents");
    setIsUploadOpen(true);
  };

  if (!user) {
    return (
      <LandingView
        onLogin={(userInfo) => {
          localStorage.setItem("aidrive_user", JSON.stringify(userInfo));
          setUser(userInfo);
        }}
      />
    );
  }

  return (
    <div className="bg-surface text-on-surface min-h-screen flex overflow-hidden font-sans" id="applet-root">

      {/* Side Navigation Bar (Logo & Menus perfectly consistent) */}
      <aside className="fixed left-0 top-0 h-full w-[280px] bg-white border-r border-outline-variant flex flex-col py-6 z-50 shadow-sm" id="main-sidebar">

        {/* Unified App Logo Block */}
        <div className="px-6 mb-8 flex items-center gap-3 cursor-pointer" onClick={() => setActiveTab("dashboard")} id="sidebar-logo">
          <div className="w-10 h-10 bg-primary rounded-xl flex items-center justify-center text-white shadow-md shadow-primary/20">
            <HardDrive className="w-5.5 h-5.5" />
          </div>
          <div>
            <h1 className="text-xl font-bold text-primary font-sans leading-tight">AI Drive</h1>
            <p className="text-[10px] text-outline font-bold tracking-widest uppercase mt-0.5">지능형 문서 관리</p>
          </div>
        </div>

        {/* Action Button */}
        <div className="px-4 mb-8">
          <button
            onClick={handleUploadClickOnSidebar}
            className="w-full py-3.5 bg-primary text-white rounded-xl font-bold text-label-md flex items-center justify-center gap-2 shadow-lg shadow-primary/15 hover:bg-opacity-95 transition-all cursor-pointer hover:scale-[1.01] active:scale-95"
            id="btn-sidebar-upload"
          >
            <Plus className="w-4 h-4 stroke-[2.5]" />
            새 문서 업로드
          </button>
        </div>

        {/* Korean Menus list */}
        <nav className="flex-1 flex flex-col gap-1 px-3" id="sidebar-nav-menu">
          <button
            onClick={() => setActiveTab("dashboard")}
            className={`flex items-center gap-3.5 px-4 py-3.5 rounded-xl font-semibold text-label-md transition-all cursor-pointer ${
              activeTab === "dashboard"
                ? "bg-surface-variant text-primary border-l-4 border-secondary shadow-sm"
                : "text-on-surface-variant hover:bg-surface-container-low"
            }`}
          >
            <LayoutDashboard className="w-5 h-5" />
            대시보드
          </button>

          <button
            onClick={() => setActiveTab("documents")}
            className={`flex items-center gap-3.5 px-4 py-3.5 rounded-xl font-semibold text-label-md transition-all cursor-pointer ${
              activeTab === "documents"
                ? "bg-surface-variant text-primary border-l-4 border-secondary shadow-sm"
                : "text-on-surface-variant hover:bg-surface-container-low"
            }`}
          >
            <FileText className="w-5 h-5" />
            내 문서
          </button>

          <button
            onClick={() => setActiveTab("chat")}
            className={`flex items-center gap-3.5 px-4 py-3.5 rounded-xl font-semibold text-label-md transition-all cursor-pointer ${
              activeTab === "chat"
                ? "bg-surface-variant text-primary border-l-4 border-secondary shadow-sm"
                : "text-on-surface-variant hover:bg-surface-container-low"
            }`}
          >
            <Sparkles className="w-5 h-5" />
            AI 채팅 및 검색
          </button>

          <button
            onClick={() => setActiveTab("settings")}
            className={`flex items-center gap-3.5 px-4 py-3.5 rounded-xl font-semibold text-label-md transition-all cursor-pointer ${
              activeTab === "settings"
                ? "bg-surface-variant text-primary border-l-4 border-secondary shadow-sm"
                : "text-on-surface-variant hover:bg-surface-container-low"
            }`}
          >
            <Settings className="w-5 h-5" />
            설정
          </button>

          {/* role이 ADMIN일 때만 노출 (Req.5~12). 지금은 로그인이 mock이라 role이 항상
              고정값이라 실제로 보이려면 로컬에서 role을 "ADMIN"으로 바꿔 테스트해야 한다 —
              실 OAuth+role 연동은 별도 이슈. */}
          {user?.role === "ADMIN" && (
            <button
              onClick={() => setActiveTab("admin")}
              className={`flex items-center gap-3.5 px-4 py-3.5 rounded-xl font-semibold text-label-md transition-all cursor-pointer ${
                activeTab === "admin"
                  ? "bg-surface-variant text-primary border-l-4 border-secondary shadow-sm"
                  : "text-on-surface-variant hover:bg-surface-container-low"
              }`}
            >
              <ShieldCheck className="w-5 h-5" />
              관리자
            </button>
          )}
        </nav>

        {/* Sidebar Footer */}
        <div className="mt-auto px-3 space-y-1 pt-6 border-t border-outline-variant/30" id="sidebar-footer">
          <button
            onClick={() => alert("AI Drive 지능형 헬프 데스크로 연결합니다.")}
            className="w-full flex items-center gap-3 px-4 py-3 text-on-surface-variant hover:bg-surface-container-low rounded-xl transition-all cursor-pointer font-semibold text-body-sm"
          >
            <HelpCircle className="w-5 h-5 text-outline" />
            도움말
          </button>

          <button
            onClick={() => {
              localStorage.removeItem("aidrive_user");
              setUser(null);
            }}
            className="w-full flex items-center gap-3 px-4 py-3 text-rose-500 hover:bg-rose-50 rounded-xl transition-all cursor-pointer font-semibold text-body-sm"
          >
            <LogOut className="w-5 h-5" />
            로그아웃
          </button>
        </div>
      </aside>

      {/* Top Application Bar & Main View Area */}
      <div className="ml-[280px] w-[calc(100%-280px)] h-screen flex flex-col overflow-hidden" id="main-content-layout">

        {/* Shared Top App Bar */}
        <header className="h-16 border-b border-outline-variant bg-white/80 backdrop-blur-md flex justify-between items-center px-8 shrink-0 z-40" id="top-appbar">
          {/* Header Search Box */}
          <div className="flex-1 max-w-xl">
            <div className="relative group">
              <Search className="w-5 h-5 absolute left-3.5 top-1/2 -translate-y-1/2 text-outline group-focus-within:text-primary transition-colors" />
              <input
                type="text"
                value={globalSearch}
                onChange={handleGlobalSearchChange}
                placeholder="어느 화면에서든 파일 제목 또는 AI 추출 단어를 검색..."
                className="w-full bg-surface-container-low border border-outline-variant rounded-full py-2 pl-11 pr-4 focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary text-body-sm font-medium transition-all"
                id="global-search-input"
              />
            </div>
          </div>

          {/* User profile actions */}
          <div className="flex items-center gap-4" id="header-user-actions">
            <button
              onClick={() => alert("현재 들어온 중요 분석 알림이 없습니다.")}
              className="w-10 h-10 flex items-center justify-center rounded-full text-on-surface-variant hover:bg-surface-container transition-colors relative cursor-pointer"
              title="알림"
            >
              <Bell className="w-5 h-5" />
              <span className="absolute top-2.5 right-2.5 w-1.5 h-1.5 bg-rose-500 rounded-full animate-ping"></span>
            </button>
            <button
              onClick={() => alert("협업 중인 타 부서 공유 드라이브 링크 모음")}
              className="w-10 h-10 flex items-center justify-center rounded-full text-on-surface-variant hover:bg-surface-container transition-colors cursor-pointer"
              title="앱 연결"
            >
              <Grid className="w-5 h-5" />
            </button>

            <div className="h-6 w-px bg-outline-variant mx-1"></div>

            <div className="flex items-center gap-3 pl-1" id="user-info-badge">
              <div className="text-right">
                <p className="font-bold text-label-md text-on-surface leading-none">{user?.name || "사용자"}님</p>
                <p className="text-[10px] text-outline font-extrabold uppercase mt-1 tracking-wider">{user?.role || "Premium Plan"}</p>
              </div>
            </div>
          </div>
        </header>

        {/* Dynamic Canvas Routing */}
        <main className="flex-1 overflow-y-auto p-8 bg-surface-bright" id="main-scrollable-area">
          <AnimatePresence mode="wait">
            {activeTab === "dashboard" && (
              <motion.div
                key="dashboard"
                initial={{ opacity: 0, y: 15 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -15 }}
                transition={{ duration: 0.25 }}
              >
                <DashboardView
                  documents={documents}
                  onNavigateToChat={handleNavigateToChat}
                  onNavigateToTab={setActiveTab}
                />
              </motion.div>
            )}

            {activeTab === "documents" && (
              <motion.div
                key="documents"
                initial={{ opacity: 0, y: 15 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -15 }}
                transition={{ duration: 0.25 }}
              >
                <MyDocumentsView
                  documents={documents}
                  onUploadDocument={handleUploadDocument}
                  onNavigateToChat={handleNavigateToChat}
                  isUploadOpen={isUploadOpen}
                  setIsUploadOpen={setIsUploadOpen}
                  onUpdateDocuments={setDocuments}
                />
              </motion.div>
            )}

            {activeTab === "chat" && (
              <motion.div
                key="chat"
                initial={{ opacity: 0, y: 15 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -15 }}
                transition={{ duration: 0.25 }}
              >
                <AIChatView
                  documents={documents}
                  chatSessions={chatSessions}
                  activeChatSessionId={activeChatSessionId}
                  onSelectChatSession={setActiveChatSessionId}
                  onNewChatTab={handleNewChatTab}
                  onCloseChatTab={handleCloseChatTab}
                  onRenameChatTab={handleRenameChatTab}
                  onToggleDocSelection={handleToggleDocSelection}
                  onSendMessage={handleSendMessage}
                  isLoadingChat={isLoadingChat}
                />
              </motion.div>
            )}

            {activeTab === "settings" && (
              <motion.div
                key="settings"
                initial={{ opacity: 0, y: 15 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -15 }}
                transition={{ duration: 0.25 }}
              >
                <SettingsView
                  user={user}
                  committedSettings={committedSettings}
                  onSaveSettings={handleSaveSettings}
                />
              </motion.div>
            )}

            {activeTab === "admin" && user?.role === "ADMIN" && (
              <motion.div
                key="admin"
                initial={{ opacity: 0, y: 15 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -15 }}
                transition={{ duration: 0.25 }}
              >
                <AdminView />
              </motion.div>
            )}
          </AnimatePresence>
        </main>
      </div>
    </div>
  );
}
