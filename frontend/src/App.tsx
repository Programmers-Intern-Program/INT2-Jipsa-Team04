import React, { useState, useEffect, useRef } from "react";
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
import type { Document, AISettings, ChatMessage, ChatSession, SessionUser, MeResponse } from "./types";

import { getUserSettings, updateUserSettings } from "./api/userSettings";
import { loginWithGoogle, logout as logoutApi } from "./api/auth";
import { getMe } from "./api/me";
import { TOKEN_ROLE_CHANGED_EVENT } from "./api/client";
import {
  OAUTH_CALLBACK_PATH,
  clearOAuthState,
  clearOAuthCodeVerifier,
  getOAuthCodeVerifier,
  verifyOAuthState,
} from "./utils/oauth";
import { listAllFiles } from "./api/files";
import { useUploads } from "./upload/UploadProvider";
import { fetchWithRetry } from "./utils/retry";

const TOKEN_KEY = "aidrive_token";
const REFRESH_TOKEN_KEY = "aidrive_refresh_token";
const USER_KEY = "aidrive_user";

/** MeResponse(백엔드) → 프론트 세션 사용자. email은 백엔드가 주지 않아 담지 않는다. */
function toSessionUser(me: MeResponse): SessionUser {
  return {
    name: me.name,
    role: me.role,
    userId: me.userId,
    profileImageUrl: me.profileImageUrl,
    status: me.status,
  };
}

/** 로그인 관련 localStorage 정리(토큰·리프레시·사용자). */
function clearAuthStorage() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

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
  const [user, setUser] = useState<SessionUser | null>(() => {
    // 토큰 없이 저장된 사용자(예: 예전 mock 로그인 찌꺼기)는 복원하지 않는다.
    // aidrive_token이 있어야만 aidrive_user를 신뢰하고, 없으면 랜딩으로 보낸다.
    if (!localStorage.getItem(TOKEN_KEY)) return null;

    const saved = localStorage.getItem(USER_KEY);
    if (!saved) return null;
    try {
      return JSON.parse(saved) as SessionUser;
    } catch {
      // aidrive_user가 깨져 있어도 첫 렌더에서 크래시하지 않도록 제거하고 로그아웃 상태로 시작한다.
      localStorage.removeItem(USER_KEY);
      return null;
    }
  });
  // OAuth 콜백 처리 중이거나(=/oauth/callback), 저장된 토큰으로 세션 복원 중일 때 true.
  // 이 동안에는 랜딩/메인 대신 로딩 화면을 보여줘 깜빡임을 막는다.
  const [authLoading, setAuthLoading] = useState<boolean>(
    () => window.location.pathname === OAUTH_CALLBACK_PATH || localStorage.getItem(TOKEN_KEY) !== null
  );
  const [activeTab, setActiveTab] = useState<string>("dashboard");
  const [documents, setDocuments] = useState<Document[]>([]);
  const { uploadedSignal } = useUploads();
  const [chatSessions, setChatSessions] = useState<ChatSession[]>(() => [
    createChatSession(getInitialSelectedDocIds([]))
  ]);
  const [activeChatSessionId, setActiveChatSessionId] = useState<string>(() => chatSessions[0].id);
  const [committedSettings, setCommittedSettings] = useState<AISettings>({ sensitivity: 0.85, voiceModel: "Nova (명확하고 신뢰감 있는)", responseStyle: "간결형", instantSummary: true, autoHighlight: false, pushNotification: true });
  const [isUploadOpen, setIsUploadOpen] = useState(false);
  const [isLoadingChat, setIsLoadingChat] = useState(false);
  const [globalSearch, setGlobalSearch] = useState("");

  // StrictMode(dev)에서 아래 useEffect가 2번 실행돼 같은 authorization code가 두 번
  // 교환(POST /auth/oauth/google)되는 것을 막는다. ref는 StrictMode의 mount→cleanup→mount
  // 재실행 사이에도 같은 컴포넌트 인스턴스에서 유지되므로 최초 1회만 처리된다.
  const authInitialized = useRef(false);

  // 로그인 상태 초기화(앱 mount 1회):
  //  (1) /oauth/callback로 복귀한 경우 → code/state 검증 후 토큰 교환·사용자 조회
  //  (2) 이미 토큰이 있는 경우 → getMe로 세션 복원(실패 시 토큰·사용자 정리)
  useEffect(() => {
    if (authInitialized.current) return;
    authInitialized.current = true;

    const isCallback = window.location.pathname === OAUTH_CALLBACK_PATH;

    async function handleOAuthCallback() {
      const params = new URLSearchParams(window.location.search);
      const error = params.get("error");
      const code = params.get("code");
      const state = params.get("state");
      try {
        if (error) throw new Error(`Google 인증이 거부되었습니다 (${error}).`);
        if (!code) throw new Error("authorization code가 없습니다.");
        // 기존 동작 유지: state 검증 실패 시 백엔드로 code를 보내지 않는다.
        if (!verifyOAuthState(state)) throw new Error("state 검증에 실패했습니다.");
        // PKCE: 로그인 시작 시 저장한 code_verifier가 없으면 교환을 진행하지 않는다.
        const codeVerifier = getOAuthCodeVerifier();
        if (!codeVerifier) throw new Error("PKCE code_verifier가 없습니다.");

        const result = await loginWithGoogle(code, codeVerifier);
        localStorage.setItem(TOKEN_KEY, result.accessToken);
        localStorage.setItem(REFRESH_TOKEN_KEY, result.refreshToken);

        const me = await getMe();
        const sessionUser = toSessionUser(me);
        localStorage.setItem(USER_KEY, JSON.stringify(sessionUser));
        setUser(sessionUser);
      } catch (err) {
        console.warn("[auth] OAuth 콜백 처리 실패 - 로그인 상태를 초기화합니다:", err);
        clearAuthStorage();
        setUser(null);
      } finally {
        // state·code_verifier는 성공/실패와 무관하게 1회용으로 폐기하고,
        // 콜백 쿼리스트링을 URL에서 제거해 메인으로 정리한다.
        clearOAuthState();
        clearOAuthCodeVerifier();
        window.history.replaceState({}, "", "/");
        setAuthLoading(false);
      }
    }

    async function restoreSession() {
      try {
        const me = await getMe();
        const sessionUser = toSessionUser(me);
        localStorage.setItem(USER_KEY, JSON.stringify(sessionUser));
        setUser(sessionUser);
      } catch (err) {
        console.warn("[auth] 세션 복원 실패 - 토큰이 만료/무효하여 로그아웃 처리합니다:", err);
        clearAuthStorage();
        setUser(null);
      } finally {
        setAuthLoading(false);
      }
    }

    if (isCallback) {
      handleOAuthCallback();
    } else if (localStorage.getItem(TOKEN_KEY)) {
      restoreSession();
    }
    // isCallback도 아니고 토큰도 없으면 authLoading 초기값이 이미 false다.
  }, []);

  // 관리자가 내 role을 바꾸면 apiFetch가 새 토큰을 조용히 저장하면서 이 이벤트를 발화한다
  // (api/client.ts 참고). 토큰만 바뀌고 이 user 상태는 그대로 두면, 관리자 메뉴 노출 여부 등
  // role 기반 화면이 새로고침 전까지 안 바뀌는 문제가 있어 getMe()를 다시 불러 동기화한다.
  useEffect(() => {
    function handleTokenRoleChanged() {
      getMe()
        .then((me) => {
          const sessionUser = toSessionUser(me);
          localStorage.setItem(USER_KEY, JSON.stringify(sessionUser));
          setUser(sessionUser);
        })
        .catch((err) => {
          console.warn("[auth] role 변경 감지 후 사용자 정보 갱신 실패:", err);
        });
    }

    window.addEventListener(TOKEN_ROLE_CHANGED_EVENT, handleTokenRoleChanged);
    return () => window.removeEventListener(TOKEN_ROLE_CHANGED_EVENT, handleTokenRoleChanged);
  }, []);

  // 실제 설정 조회 시도 — user가 아직 null일 때(마운트 시점, OAuth 콜백으로 로그인 처리
  // 중이라 토큰이 저장되기 전)는 아예 시도하지 않고 건너뛴다. user가 채워지는 시점
  // (로그인 완료/세션 복원)에 맞춰 시도하도록 [user] 의존성을 쓴다. 이 시점엔 로그인이
  // 확정된 상태라 실패하면 "비로그인이라 401"이 아니라 진짜 오류이므로, mock으로 조용히
  // 가리지 않고 재시도(fetchWithRetry)한다 — 그래도 실패하면 fallback 기본값을 유지한다.
  useEffect(() => {
    if (!user) return;
    fetchWithRetry(getUserSettings)
      .then(setCommittedSettings)
      .catch((err) => {
        console.error("[settings] GET /api/v1/users/me/settings 재시도 후에도 실패:", err);
      });
  }, [user]);

  // 실제 문서 목록 조회 시도 — 위 설정 조회와 같은 이유로 [user] 의존성과 재시도를 쓴다.
  // 로그인이 확정된 뒤의 실패는 실제 오류이므로, mock 파일 목록으로 가리는 대신 재시도하고
  // 그래도 실패하면 빈 목록 상태를 유지한다(실제로는 있는데 안 보이는 것보다, 없는 척
  // 보여주는 mock 쪽이 로그인된 사용자에게 더 혼란스럽다고 판단).
  useEffect(() => {
    if (!user) return;
    fetchWithRetry(listAllFiles)
      .then(setDocuments)
      .catch((err) => {
        console.error("[files] GET /api/v1/files 재시도 후에도 실패:", err);
      });
  }, [user]);

  useEffect(() => {
    if (uploadedSignal === 0) return;
    listAllFiles()
        .then(setDocuments)
        .catch((err) => {
          console.error("[uploads] 업로드 후 목록 재동기화 실패:", err);
        });
  }, [uploadedSignal]);

  // 로그아웃: 가능하면 refresh token 폐기 API를 호출하고(실패해도 무시),
  // 항상 localStorage(토큰·리프레시·사용자)를 정리한 뒤 랜딩으로 돌아간다.
  const handleLogout = async () => {
    const refreshToken = localStorage.getItem(REFRESH_TOKEN_KEY);
    if (refreshToken) {
      try {
        await logoutApi(refreshToken);
      } catch (err) {
        console.warn("[auth] 로그아웃 API 실패 - 로컬 세션만 정리합니다:", err);
      }
    }
    clearAuthStorage();
    setUser(null);
  };

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

  // OAuth 콜백 처리/세션 복원 중에는 로딩 화면(랜딩·메인 깜빡임 방지).
  if (authLoading) {
    return (
      <div className="min-h-screen bg-surface-bright flex flex-col items-center justify-center gap-4 font-sans">
        <div className="w-12 h-12 bg-primary rounded-xl flex items-center justify-center text-white shadow-md shadow-primary/20">
          <HardDrive className="w-6 h-6" />
        </div>
        <svg className="animate-spin h-6 w-6 text-primary" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
        </svg>
        <p className="text-body-sm text-outline font-medium">로그인 처리 중...</p>
      </div>
    );
  }

  if (!user) {
    return <LandingView />;
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

          {/* role이 ADMIN일 때만 노출 (Req.5~12). role은 GET /users/me의 실제 값("USERS" 또는
              "ADMIN")이다. 관리자 메뉴를 보려면 해당 계정의 DB Role이 ADMIN이어야 한다. */}
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
            onClick={handleLogout}
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
                <p className="text-[10px] text-outline font-extrabold uppercase mt-1 tracking-wider">{user?.role || "USERS"}</p>
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
                  sensitivity={committedSettings.sensitivity}
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
