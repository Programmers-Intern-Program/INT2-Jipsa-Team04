// 백엔드 REST API 공통 fetch 래퍼.
//
// 인증 토큰: 실 로그인(OAuth) 연동은 별도 이슈라 "aidrive_token"
// 키는 지금 비어있는 게 정상이다. 로그인이 붙으면 그쪽에서 로그인 성공 시
// localStorage.setItem("aidrive_token", jwt) 만 해주면 이 클라이언트가 자동으로
// Authorization 헤더에 실어 보낸다. 지금은 토큰이 없어 모든 인증 필요 API가 401을
// 받는데, 호출부(폴더 등)에서 실패 시 기존 mock 로직으로 폴백하도록 짜여 있다.
const TOKEN_STORAGE_KEY = "aidrive_token";
const REFRESH_TOKEN_STORAGE_KEY = "aidrive_refresh_token";
const USER_STORAGE_KEY = "aidrive_user";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

function getAuthToken(): string | null {
  return localStorage.getItem(TOKEN_STORAGE_KEY);
}

function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_TOKEN_STORAGE_KEY);
}

/** 세션 만료(refresh 실패) 시 로그인 관련 localStorage(토큰·리프레시·사용자)를 정리한다. */
function clearAuthStorage(): void {
  localStorage.removeItem(TOKEN_STORAGE_KEY);
  localStorage.removeItem(REFRESH_TOKEN_STORAGE_KEY);
  localStorage.removeItem(USER_STORAGE_KEY);
}

/**
 * 저장된 JWT의 payload(sub 클레임)를 디코드해 현재 로그인한 사용자의 Users_IDX를 돌려준다.
 * JwtService.generateToken()이 subject를 userId로 발급하므로 sub == userId다.
 * 관리자 화면에서 "자기 자신" 행을 구분하려고 추가함 — GET /api/v1/users/me 응답엔 userId가
 * 없어서(API 문서.md 1장) 백엔드 계약을 건드리지 않고 프론트에서만 해결한 방식이다.
 * 서명 검증은 하지 않는다(신뢰 목적이 아니라 UI 표시용 판별일 뿐이고, 실제 권한 검증은
 * 백엔드가 매 요청마다 다시 하기 때문).
 */
export function getCurrentUserId(): number | null {
  const token = getAuthToken();
  if (!token) return null;
  try {
    const payload = token.split(".")[1];
    const json = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
    const sub = JSON.parse(json)?.sub;
    const id = Number(sub);
    return Number.isFinite(id) ? id : null;
  } catch {
    return null;
  }
}

/** 저장된 Access Token을 실어 실제 요청 1회를 수행한다(재시도용으로 재사용). */
function doFetch(path: string, method: string, body: unknown): Promise<Response> {
  const token = getAuthToken();
  return fetch(`/api/v1${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
}

/**
 * 실패 응답을 ApiError로 변환한다.
 * GlobalExceptionHandler는 {success:false, data:null, error:{code, message}} 형태로 내려준다
 * (ApiResponse.fail). data?.error를 그대로 message로 쓰면 객체가 "[object Object]"로 뭉개져서
 * admin 화면 등에서 에러 사유를 못 보여주는 문제가 있어 error.message를 우선 꺼내고,
 * 옛 포맷(error가 문자열)이거나 파싱 실패 시 폴백한다.
 */
async function toApiError(response: Response): Promise<ApiError> {
  const message = await response
    .json()
    .then((data) => data?.error?.message ?? data?.error ?? response.statusText)
    .catch(() => response.statusText);
  return new ApiError(response.status, message);
}

/**
 * POST /api/v1/auth/refresh — Refresh Token으로 새 Access Token 재발급.
 *
 * auth.ts와의 순환 import를 피하려고 apiFetch가 아니라 기본 fetch를 직접 쓴다
 * (refresh는 apiFetch의 401 재시도 로직 자체를 타면 안 되는 예외 경로이기도 하다).
 * 백엔드는 rotation 없이 accessToken만 내려주므로(응답에 refreshToken 없음) accessToken만
 * 반환하고 기존 refresh token은 그대로 둔다. 응답 형식은 {success, data:{accessToken}, error}.
 *
 * @param refreshToken 보관 중인 Refresh Token 원문
 * @returns 새로 발급된 Access Token
 * @throws ApiError 재발급 실패 시(만료/폐기/위조된 refresh token → 401 등)
 */
async function requestRefreshAccessToken(refreshToken: string): Promise<string> {
  const response = await fetch(`/api/v1/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refreshToken }),
  });

  if (!response.ok) {
    throw await toApiError(response);
  }

  const envelope = (await response.json()) as { data?: { accessToken?: string } };
  const accessToken = envelope?.data?.accessToken;
  if (!accessToken) {
    throw new ApiError(response.status, "재발급 응답에 accessToken이 없습니다.");
  }
  return accessToken;
}

// 동시에 여러 요청이 401을 받아도 refresh는 한 번만 수행하고 나머지는 같은 Promise를 기다린다
// (single-flight). settle 후 null로 초기화해 다음 만료 시점에는 다시 1회 수행한다.
let inFlightRefresh: Promise<string> | null = null;

/**
 * 저장된 refresh token으로 새 Access Token을 1회 재발급받아 aidrive_token에 저장한다.
 * 진행 중인 refresh가 있으면 새로 호출하지 않고 그 Promise를 공유한다.
 */
function refreshTokenOnce(): Promise<string> {
  if (inFlightRefresh) return inFlightRefresh;

  const refreshToken = getRefreshToken();
  if (!refreshToken) {
    return Promise.reject(new ApiError(401, "리프레시 토큰이 없습니다."));
  }

  inFlightRefresh = requestRefreshAccessToken(refreshToken)
    .then((accessToken) => {
      localStorage.setItem(TOKEN_STORAGE_KEY, accessToken);
      return accessToken;
    })
    .finally(() => {
      inFlightRefresh = null;
    });

  return inFlightRefresh;
}

/**
 * fetch 래퍼. 요청 본문은 JSON으로 직렬화하고, 응답은 JSON으로 파싱한다.
 * 응답 본문이 없는 경우(204 등)를 대비해 빈 응답도 안전하게 처리.
 *
 * Access Token 만료(401) 시: refresh token이 있으면 /auth/refresh로 재발급 후 원 요청을
 * 새 토큰으로 1회만 재시도한다. refresh 자체가 실패하면 로그인 세션을 정리하고 원래 401을 던진다.
 * 무한 루프 방지를 위해 /auth/* 엔드포인트(로그인·재발급·로그아웃)는 refresh 재시도 대상에서 제외한다.
 */
export async function apiFetch<T>(
  path: string,
  options: { method?: string; body?: unknown } = {}
): Promise<T> {
  const method = options.method ?? "GET";
  const body = options.body;

  // /auth/refresh 자신이 401을 다시 refresh하려 하면 무한 루프가 되므로 auth 계열은 제외한다.
  const isAuthEndpoint = path.startsWith("/auth/");

  let response = await doFetch(path, method, body);

  if (response.status === 401 && !isAuthEndpoint && getRefreshToken()) {
    try {
      await refreshTokenOnce();               // 새 accessToken을 aidrive_token에 저장
    } catch {
      // refresh 실패(리프레시 토큰 만료/폐기/부재) → 세션 정리 후 원래 401 전파.
      clearAuthStorage();
      throw await toApiError(response);
    }
    response = await doFetch(path, method, body);   // 재시도는 최대 1회
  }

  if (!response.ok) {
    throw await toApiError(response);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}
