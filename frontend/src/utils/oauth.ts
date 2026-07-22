// Google OAuth (authorization-code flow + PKCE) 프론트 유틸.
//
// 백엔드가 토큰 교환을 담당하는 authorization-code 방식이라, 프론트의 역할은
//   (1) 랜덤 state와 PKCE code_verifier를 만들어 sessionStorage에 보관하고
//   (2) code_verifier를 SHA-256(S256) 해싱한 code_challenge를 authorize URL에 실어 전체 페이지 이동시킨 뒤
//   (3) /oauth/callback 복귀 시 URL의 state와 저장값을 비교(CSRF 방지)하고,
//       저장해 둔 code_verifier를 백엔드 로그인 요청에 함께 넘겨 code를 이번 로그인 시도에 묶는 것이다.
// authorization code는 백엔드로 넘겨 교환하므로 client_secret은 프론트에 두지 않는다.

const STATE_STORAGE_KEY = "aidrive_oauth_state";
const VERIFIER_STORAGE_KEY = "aidrive_oauth_code_verifier";
const GOOGLE_AUTHORIZE_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth";

/** 로컬 fallback. 실제 값은 frontend/.env.local 의 VITE_GOOGLE_REDIRECT_URI 로 주입한다. */
const DEFAULT_REDIRECT_URI = "http://localhost:5173/oauth/callback";

/** 콜백을 처리할 경로. authorize URL의 redirect_uri, App의 콜백 감지와 일치해야 한다. */
export const OAUTH_CALLBACK_PATH = "/oauth/callback";

/** VITE_GOOGLE_CLIENT_ID (미설정이면 빈 문자열). 값 유무는 isOAuthConfigured로 판단. */
export function getGoogleClientId(): string {
  return (import.meta.env.VITE_GOOGLE_CLIENT_ID ?? "").trim();
}

/** VITE_GOOGLE_REDIRECT_URI (미설정이면 로컬 기본값). */
export function getRedirectUri(): string {
  const fromEnv = (import.meta.env.VITE_GOOGLE_REDIRECT_URI ?? "").trim();
  return fromEnv || DEFAULT_REDIRECT_URI;
}

/** 로그인 버튼을 눌러도 되는지(= Client ID가 주입되어 있는지). */
export function isOAuthConfigured(): boolean {
  return getGoogleClientId().length > 0;
}

/** 랜덤 state 생성 후 sessionStorage에 저장하고 반환한다. */
export function createOAuthState(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  const state = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  sessionStorage.setItem(STATE_STORAGE_KEY, state);
  return state;
}

/** 콜백에서 받은 state가 저장해 둔 값과 일치하는지 검증한다(둘 다 존재하고 같아야 true). */
export function verifyOAuthState(received: string | null): boolean {
  const saved = sessionStorage.getItem(STATE_STORAGE_KEY);
  return saved !== null && received !== null && saved === received;
}

/** 저장된 state 제거(검증 성공/실패와 무관하게 1회용으로 폐기). */
export function clearOAuthState(): void {
  sessionStorage.removeItem(STATE_STORAGE_KEY);
}

/** ArrayBuffer/Uint8Array → base64url (padding 제거, + → -, / → _). PKCE 규약(RFC 7636)용. */
function toBase64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");
}

/**
 * PKCE code_verifier 생성 후 sessionStorage에 저장하고 반환한다.
 * 32바이트 랜덤을 base64url로 인코딩(43자, [A-Za-z0-9-_] → RFC 7636 unreserved 집합)한다.
 */
export function createOAuthCodeVerifier(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  const verifier = toBase64Url(bytes);
  sessionStorage.setItem(VERIFIER_STORAGE_KEY, verifier);
  return verifier;
}

/** 저장된 code_verifier 조회(없으면 null). 콜백에서 백엔드로 넘길 때 사용. */
export function getOAuthCodeVerifier(): string | null {
  return sessionStorage.getItem(VERIFIER_STORAGE_KEY);
}

/** 저장된 code_verifier 제거(state와 함께 1회용으로 폐기). */
export function clearOAuthCodeVerifier(): void {
  sessionStorage.removeItem(VERIFIER_STORAGE_KEY);
}

/** code_verifier를 SHA-256(S256)으로 해싱해 base64url code_challenge를 만든다(async). */
export async function createCodeChallenge(verifier: string): Promise<string> {
  const data = new TextEncoder().encode(verifier);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return toBase64Url(new Uint8Array(digest));
}

/**
 * Google authorize URL 생성. state는 createOAuthState(), codeChallenge는
 * createCodeChallenge(createOAuthCodeVerifier())로 만든 값을 넘긴다.
 */
export function buildGoogleAuthorizeUrl(state: string, codeChallenge: string): string {
  const params = new URLSearchParams({
    client_id: getGoogleClientId(),
    redirect_uri: getRedirectUri(),
    response_type: "code",
    scope: "openid email profile",
    state,
    access_type: "offline",
    prompt: "consent",
    include_granted_scopes: "true",
    code_challenge: codeChallenge,
    code_challenge_method: "S256",
  });
  return `${GOOGLE_AUTHORIZE_ENDPOINT}?${params.toString()}`;
}
