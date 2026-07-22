import { useState } from "react";
import { HardDrive, ShieldCheck, Sparkles, FolderTree, MessageSquareText, AlertTriangle } from "lucide-react";
import {
  buildGoogleAuthorizeUrl,
  clearOAuthCodeVerifier,
  clearOAuthState,
  createCodeChallenge,
  createOAuthCodeVerifier,
  createOAuthState,
  isOAuthConfigured,
} from "../utils/oauth";

// Google 브랜드 마크 (lucide-react엔 브랜드 로고가 없어 인라인 SVG 사용)
function GoogleIcon() {
  return (
    <svg className="w-4 h-4" viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.9 29.3 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.1 8 3l6-6C34.6 5.1 29.6 3 24 3 12.4 3 3 12.4 3 24s9.4 21 21 21 21-9.4 21-21c0-1.4-.1-2.7-.4-3.5z" />
      <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.6 15.9 18.9 13 24 13c3.1 0 5.8 1.1 8 3l6-6C34.6 5.1 29.6 3 24 3 16.3 3 9.7 7.3 6.3 14.7z" />
      <path fill="#4CAF50" d="M24 45c5.5 0 10.4-1.9 14.3-5.1l-6.6-5.6C29.6 36 26.9 37 24 37c-5.3 0-9.7-3.1-11.3-7.5l-6.6 5C9.5 40.7 16.2 45 24 45z" />
      <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.3-2.3 4.3-4.3 5.7l6.6 5.6C41.6 36 45 30.6 45 24c0-1.4-.1-2.7-.4-3.5z" />
    </svg>
  );
}

export default function LandingView() {
  const [isLoading, setIsLoading] = useState(false);
  const [configError, setConfigError] = useState<string | null>(null);

  // 실제 Google OAuth authorization-code + PKCE 흐름 시작:
  // state와 code_verifier를 만들어 sessionStorage에 저장하고, code_verifier의 SHA-256(S256)
  // code_challenge를 authorize URL에 실어 Google로 전체 페이지 이동한다.
  // 복귀(/oauth/callback) 이후의 code 교환·토큰 저장·사용자 조회는 App.tsx가 담당한다.
  const handleGoogleStart = async () => {
    if (!isOAuthConfigured()) {
      setConfigError(
        "Google 로그인 설정이 없습니다. frontend/.env.local 에 VITE_GOOGLE_CLIENT_ID 를 설정한 뒤 dev 서버를 다시 시작하세요."
      );
      return;
    }
    setConfigError(null);
    setIsLoading(true);
    try {
      const state = createOAuthState();
      const codeChallenge = await createCodeChallenge(createOAuthCodeVerifier());
      window.location.href = buildGoogleAuthorizeUrl(state, codeChallenge);
    } catch (err) {
      console.warn("[auth] Google 로그인 시작 실패:", err);
      // authorize URL로 이동하지 못했으므로 저장해 둔 state·code_verifier 잔여값을 정리한다.
      clearOAuthState();
      clearOAuthCodeVerifier();
      setConfigError("Google 로그인을 시작하지 못했습니다. 잠시 후 다시 시도해 주세요.");
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-surface-bright relative overflow-hidden font-sans" id="landing-container">
      {/* Dynamic abstract grid background for premium vibe */}
      <div className="absolute inset-0 bg-[radial-gradient(#e5eeff_1px,transparent_1px)] [background-size:16px_16px] opacity-60 pointer-events-none"></div>
      <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-primary/5 blur-[120px] rounded-full pointer-events-none"></div>
      <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-secondary/5 blur-[120px] rounded-full pointer-events-none"></div>

      {/* Top bar: 로고(좌) + 구글로 시작하기 버튼(우) */}
      <header className="relative z-10 flex items-center justify-between gap-4 px-6 md:px-10 py-6" id="landing-topbar">
        <div className="flex items-center gap-3" id="landing-logo-block">
          <div className="w-10 h-10 bg-primary rounded-xl flex items-center justify-center text-white shadow-md shadow-primary/20">
            <HardDrive className="w-5.5 h-5.5" />
          </div>
          <div>
            <h1 className="text-xl font-bold text-primary font-sans leading-tight">AI Drive</h1>
            <p className="text-[10px] text-outline font-bold tracking-widest uppercase mt-0.5">지능형 문서 관리</p>
          </div>
        </div>

        <button
          type="button"
          onClick={handleGoogleStart}
          disabled={isLoading}
          className="flex items-center gap-2 py-2.5 px-4 bg-white border border-outline-variant rounded-xl font-bold text-xs text-on-surface shadow-sm hover:shadow-md hover:bg-surface-container-low transition-all cursor-pointer active:scale-95 disabled:opacity-50"
          id="btn-google-start"
        >
          {isLoading ? (
            <svg className="animate-spin h-4 w-4 text-primary" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
            </svg>
          ) : (
            <GoogleIcon />
          )}
          {isLoading ? "로그인 중..." : "Google로 시작하기"}
        </button>
      </header>

      {/* Hero */}
      <main className="relative z-10 max-w-5xl mx-auto px-6 md:px-10 pt-12 md:pt-20 pb-24 text-center" id="landing-hero">
        <span className="inline-block text-[10px] bg-primary/10 text-primary px-2.5 py-1 rounded-full font-bold uppercase tracking-widest border border-primary/10">
          Smart AI Cloud
        </span>
        <h2 className="mt-5 text-3xl md:text-5xl font-extrabold tracking-tight leading-tight text-on-surface">
          모두를 위한 지능형<br />스마트 문서 드라이브
        </h2>
        <p className="mt-5 text-sm md:text-base text-outline leading-relaxed max-w-xl mx-auto">
          인공지능 RAG 검색 엔진, 스마트 개인정보 보호, 맞춤형 자동 정리까지 — 구글 계정 하나로 바로 시작해 보세요.
        </p>

        <div className="mt-8 flex justify-center">
          <button
            type="button"
            onClick={handleGoogleStart}
            disabled={isLoading}
            className="flex items-center gap-2.5 py-3.5 px-6 bg-primary text-white rounded-xl font-bold text-sm shadow-lg shadow-primary/15 hover:bg-opacity-95 transition-all cursor-pointer active:scale-95 disabled:opacity-50"
            id="btn-google-start-hero"
          >
            <span className="w-5 h-5 bg-white rounded-full flex items-center justify-center shrink-0">
              <GoogleIcon />
            </span>
            {isLoading ? "로그인 중..." : "Google로 시작하기"}
          </button>
        </div>

        {/* 설정 누락 에러 (VITE_GOOGLE_CLIENT_ID 미설정 시) */}
        {configError && (
          <div
            className="mt-6 mx-auto max-w-xl flex items-start gap-2.5 text-left bg-rose-50 border border-rose-200 text-rose-700 rounded-xl px-4 py-3"
            id="landing-config-error"
            role="alert"
          >
            <AlertTriangle className="w-4.5 h-4.5 shrink-0 mt-0.5" />
            <p className="text-xs leading-relaxed font-medium">{configError}</p>
          </div>
        )}

        {/* Core features */}
        <div className="mt-16 grid grid-cols-1 md:grid-cols-3 gap-4 text-left">
          <div className="p-5 bg-white rounded-2xl border border-outline-variant/60 shadow-sm">
            <div className="w-9 h-9 rounded-full bg-primary/10 flex items-center justify-center mb-3">
              <ShieldCheck className="w-4.5 h-4.5 text-primary" />
            </div>
            <h3 className="text-sm font-bold text-on-surface mb-1">실시간 보안 분류</h3>
            <p className="text-xs text-outline leading-relaxed">PII 개인정보를 자동 감지해 보안 등급을 분류합니다.</p>
          </div>
          <div className="p-5 bg-white rounded-2xl border border-outline-variant/60 shadow-sm">
            <div className="w-9 h-9 rounded-full bg-primary/10 flex items-center justify-center mb-3">
              <FolderTree className="w-4.5 h-4.5 text-primary" />
            </div>
            <h3 className="text-sm font-bold text-on-surface mb-1">AI 자동 정리</h3>
            <p className="text-xs text-outline leading-relaxed">부서별/보안별/프로젝트별 등 다차원 기준으로 폴더를 정리합니다.</p>
          </div>
          <div className="p-5 bg-white rounded-2xl border border-outline-variant/60 shadow-sm">
            <div className="w-9 h-9 rounded-full bg-primary/10 flex items-center justify-center mb-3">
              <MessageSquareText className="w-4.5 h-4.5 text-primary" />
            </div>
            <h3 className="text-sm font-bold text-on-surface mb-1">RAG 챗봇 검색</h3>
            <p className="text-xs text-outline leading-relaxed">문서 내용을 근거로 답하는 AI 채팅 검색을 제공합니다.</p>
          </div>
        </div>

        <p className="mt-10 flex items-center justify-center gap-1.5 text-[11px] text-outline">
          <Sparkles className="w-3.5 h-3.5" />
          비밀번호 없이, 구글 계정으로만 로그인합니다.
        </p>
      </main>
    </div>
  );
}
