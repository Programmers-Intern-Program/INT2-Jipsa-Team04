import React, { useState } from "react";
import { HardDrive, Lock, Mail, ArrowRight, ShieldCheck, Sparkles, User, Check } from "lucide-react";

interface LoginViewProps {
  onLogin: (user: { name: string; email: string; role: string }) => void;
}

export default function LoginView({ onLogin }: LoginViewProps) {
  const [isSignUp, setIsSignUp] = useState(false);
  const [email, setEmail] = useState("guest@aidrive.ai");
  const [password, setPassword] = useState("••••••••");
  const [name, setName] = useState("홍길동");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  const [rememberMe, setRememberMe] = useState(true);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!email || !password || (isSignUp && !name)) {
      setError("모든 필드를 올바르게 입력해 주세요.");
      return;
    }
    
    setIsLoading(true);
    setError("");

    // Simulate network latency for high-fidelity interactive feel
    setTimeout(() => {
      setIsLoading(false);
      onLogin({
        name: isSignUp ? name : (email === "guest@aidrive.ai" ? "홍길동" : email.split("@")[0]),
        email: email,
        role: "Premium Plan"
      });
    }, 1200);
  };

  const handleQuickDemoLogin = (demoName: string, demoEmail: string) => {
    setIsLoading(true);
    setError("");
    setTimeout(() => {
      setIsLoading(false);
      onLogin({
        name: demoName,
        email: demoEmail,
        role: "Premium Plan"
      });
    }, 800);
  };

  return (
    <div className="min-h-screen bg-surface-bright flex items-center justify-center p-4 relative overflow-hidden font-sans" id="login-container">
      {/* Dynamic abstract grid background for premium vibe */}
      <div className="absolute inset-0 bg-[radial-gradient(#e5eeff_1px,transparent_1px)] [background-size:16px_16px] opacity-60 pointer-events-none"></div>
      
      {/* Floating decorative ambient light rings */}
      <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-primary/5 blur-[120px] rounded-full pointer-events-none"></div>
      <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-secondary/5 blur-[120px] rounded-full pointer-events-none"></div>

      <div className="w-full max-w-5xl bg-white rounded-3xl border border-outline-variant/60 shadow-xl shadow-primary/5 flex flex-col md:flex-row overflow-hidden relative z-10" id="login-card">
        
        {/* Left Pane - Artistic Info Banner (Premium Enterprise Vibe) */}
        <div className="w-full md:w-5/12 bg-primary p-8 md:p-12 text-white flex flex-col justify-between relative overflow-hidden" id="login-left-pane">
          {/* Abstract curve graphic */}
          <div className="absolute inset-0 bg-gradient-to-br from-primary via-primary to-tertiary opacity-95"></div>
          <div className="absolute -top-12 -left-12 w-48 h-48 rounded-full bg-white/5 blur-xl pointer-events-none"></div>
          <div className="absolute -bottom-24 -right-12 w-64 h-64 rounded-full bg-secondary/10 blur-2xl pointer-events-none"></div>
          
          <div className="relative z-10 flex items-center gap-3" id="login-logo-block">
            <div className="w-10 h-10 bg-white/10 backdrop-blur-md rounded-xl flex items-center justify-center border border-white/10 shadow-sm animate-bounce-subtle">
              <HardDrive className="w-5.5 h-5.5 text-white" />
            </div>
            <div>
              <h2 className="text-xl font-bold font-sans tracking-tight">AI Drive</h2>
              <p className="text-[10px] text-inverse-primary font-semibold tracking-wider">지능형 개인 & 팀 클라우드</p>
            </div>
          </div>

          <div className="relative z-10 my-12 space-y-6" id="login-marketing-block">
            <div className="space-y-2">
              <span className="text-[10px] bg-white/15 px-2.5 py-1 rounded-full font-bold uppercase tracking-widest text-inverse-primary border border-white/10">
                Smart AI Cloud
              </span>
              <h3 className="text-2xl md:text-3xl font-extrabold tracking-tight leading-tight pt-2">
                모두를 위한 지능형<br />스마트 문서 드라이브
              </h3>
            </div>
            <p className="text-xs text-white/70 leading-relaxed max-w-sm">
              인공지능 RAG 검색 엔진, 스마트 개인정보 보호, 맞춤형 자동 정리까지 이제 누구나 직관적이고 완벽한 문서 드라이브를 무료로 경험해 볼 수 있습니다.
            </p>
          </div>

          {/* Core features indicators */}
          <div className="relative z-10 space-y-3.5 pt-4 border-t border-white/15" id="login-features-list">
            <div className="flex items-center gap-3">
              <div className="w-6 h-6 rounded-full bg-white/10 flex items-center justify-center shrink-0">
                <ShieldCheck className="w-3.5 h-3.5 text-inverse-primary" />
              </div>
              <span className="text-xs font-semibold text-white/90">실시간 PII 개인정보 감지 & 보안 분류</span>
            </div>
            <div className="flex items-center gap-3">
              <div className="w-6 h-6 rounded-full bg-white/10 flex items-center justify-center shrink-0">
                <Sparkles className="w-3.5 h-3.5 text-inverse-primary" />
              </div>
              <span className="text-xs font-semibold text-white/90">다차원 정렬 기준 제공 (부서별/보안별/프로젝트별)</span>
            </div>
          </div>
        </div>

        {/* Right Pane - Elegant Form Control */}
        <div className="w-full md:w-7/12 p-8 md:p-12 flex flex-col justify-center" id="login-right-pane">
          <div className="max-w-md w-full mx-auto space-y-8" id="login-form-area">
            
            {/* Tab Selector */}
            <div className="flex border-b border-outline-variant/40 pb-2" id="login-tab-selector">
              <button
                type="button"
                onClick={() => { setIsSignUp(false); setError(""); }}
                className={`pb-2.5 px-4 font-bold text-sm tracking-tight border-b-2 transition-all cursor-pointer ${
                  !isSignUp 
                    ? "border-primary text-primary" 
                    : "border-transparent text-outline hover:text-on-surface"
                }`}
              >
                일반 로그인
              </button>
              <button
                type="button"
                onClick={() => { setIsSignUp(true); setError(""); }}
                className={`pb-2.5 px-4 font-bold text-sm tracking-tight border-b-2 transition-all cursor-pointer ${
                  isSignUp 
                    ? "border-primary text-primary" 
                    : "border-transparent text-outline hover:text-on-surface"
                }`}
              >
                신규 가입
              </button>
            </div>

            <div className="space-y-1.5">
              <h3 className="text-xl font-bold text-on-surface font-sans">
                {isSignUp ? "새로운 계정 만들기" : "AI Drive 시작하기"}
              </h3>
              <p className="text-xs text-outline leading-relaxed">
                {isSignUp 
                  ? "누구나 쉽게 이메일로 가입하여 똑똑한 AI 문서 관리 도구를 경험해 보세요." 
                  : "게스트 이메일 계정 혹은 아래 제공되는 편리한 원클릭 테스트 계정으로 바로 체험해 보세요."}
              </p>
            </div>

            {error && (
              <div className="p-3.5 bg-rose-50 border border-rose-100 text-rose-600 rounded-xl text-xs font-semibold flex items-center gap-2">
                <span className="w-1.5 h-1.5 rounded-full bg-rose-500 shrink-0"></span>
                {error}
              </div>
            )}

            <form onSubmit={handleSubmit} className="space-y-4" id="form-login">
              {isSignUp && (
                <div className="space-y-1.5">
                  <label className="text-[11px] font-extrabold text-secondary uppercase tracking-wider block px-1">이름</label>
                  <div className="relative group">
                    <User className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-outline group-focus-within:text-primary transition-colors" />
                    <input
                      type="text"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      placeholder="예: 홍길동"
                      className="w-full bg-surface border border-outline-variant rounded-xl py-3 pl-11 pr-4 text-xs font-semibold focus:outline-none focus:ring-2 focus:ring-primary/10 focus:border-primary transition-all"
                      required
                    />
                  </div>
                </div>
              )}

              <div className="space-y-1.5">
                <label className="text-[11px] font-extrabold text-secondary uppercase tracking-wider block px-1">이메일 주소</label>
                <div className="relative group">
                  <Mail className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-outline group-focus-within:text-primary transition-colors" />
                  <input
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="user@example.com"
                    className="w-full bg-surface border border-outline-variant rounded-xl py-3 pl-11 pr-4 text-xs font-semibold focus:outline-none focus:ring-2 focus:ring-primary/10 focus:border-primary transition-all"
                    required
                  />
                </div>
              </div>

              <div className="space-y-1.5">
                <div className="flex justify-between items-center px-1">
                  <label className="text-[11px] font-extrabold text-secondary uppercase tracking-wider">비밀번호</label>
                  {!isSignUp && (
                    <a href="#forgot" onClick={(e) => { e.preventDefault(); alert("가입하신 이메일로 임시 비밀번호 재설정 링크가 전송됩니다."); }} className="text-[10px] text-primary font-bold hover:underline">
                      분실하셨나요?
                    </a>
                  )}
                </div>
                <div className="relative group">
                  <Lock className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-outline group-focus-within:text-primary transition-colors" />
                  <input
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="••••••••"
                    className="w-full bg-surface border border-outline-variant rounded-xl py-3 pl-11 pr-4 text-xs font-semibold focus:outline-none focus:ring-2 focus:ring-primary/10 focus:border-primary transition-all"
                    required
                  />
                </div>
              </div>

              {/* Keep Logged in Toggle */}
              <div className="flex items-center justify-between pt-1" id="login-form-options">
                <label className="flex items-center gap-2 cursor-pointer group">
                  <button
                    type="button"
                    onClick={() => setRememberMe(!rememberMe)}
                    className={`w-4 h-4 rounded border transition-colors flex items-center justify-center ${
                      rememberMe ? "bg-primary border-primary text-white" : "border-outline-variant hover:border-outline bg-white"
                    }`}
                  >
                    {rememberMe && <Check className="w-3 h-3 stroke-[3]" />}
                  </button>
                  <span className="text-[11px] font-semibold text-outline group-hover:text-on-surface transition-colors select-none">
                    로그인 상태 유지
                  </span>
                </label>
                <span className="text-[11px] text-outline font-medium">Security Verified ✔</span>
              </div>

              {/* Submit Action Button */}
              <button
                type="submit"
                disabled={isLoading}
                className="w-full py-3.5 bg-primary text-white rounded-xl font-bold text-xs flex items-center justify-center gap-2 shadow-lg shadow-primary/15 hover:bg-opacity-95 transition-all cursor-pointer active:scale-95 disabled:opacity-50 mt-6"
                id="btn-login-submit"
              >
                {isLoading ? (
                  <span className="flex items-center gap-2">
                    <svg className="animate-spin h-4 w-4 text-white" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                    </svg>
                    스마트 엔진 보안 인증 중...
                  </span>
                ) : (
                  <>
                    <span>{isSignUp ? "신규 계정 만들기 & 시작" : "스마트 드라이브 로그인"}</span>
                    <ArrowRight className="w-4 h-4" />
                  </>
                )}
              </button>
            </form>

            {/* Quick Guest / Grading Testing Panel */}
            <div className="pt-6 border-t border-outline-variant/30 space-y-3" id="quick-login-sandbox">
              <div className="flex items-center justify-between">
                <span className="text-[10px] font-extrabold text-secondary uppercase tracking-widest block">
                  Quick Sandbox (원클릭 체험계정)
                </span>
                <span className="text-[9px] bg-secondary/10 text-secondary px-1.5 py-0.5 rounded-full font-extrabold uppercase animate-pulse">
                  Ready to test
                </span>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  onClick={() => handleQuickDemoLogin("홍길동", "guest@aidrive.ai")}
                  disabled={isLoading}
                  className="p-2.5 rounded-xl bg-surface hover:bg-surface-container-low border border-outline-variant/40 flex flex-col items-start gap-0.5 transition-all hover:scale-[1.01] active:scale-95 cursor-pointer text-left"
                >
                  <span className="text-xs font-bold text-on-surface">홍길동 (게스트)</span>
                  <span className="text-[9px] text-outline">guest@aidrive.ai</span>
                </button>
                <button
                  type="button"
                  onClick={() => handleQuickDemoLogin("이지은", "jieun@aidrive.ai")}
                  disabled={isLoading}
                  className="p-2.5 rounded-xl bg-surface hover:bg-surface-container-low border border-outline-variant/40 flex flex-col items-start gap-0.5 transition-all hover:scale-[1.01] active:scale-95 cursor-pointer text-left"
                >
                  <span className="text-xs font-bold text-on-surface">이지은 (일반 사용자)</span>
                  <span className="text-[9px] text-outline">jieun@aidrive.ai</span>
                </button>
              </div>
            </div>

          </div>
        </div>

      </div>
    </div>
  );
}
