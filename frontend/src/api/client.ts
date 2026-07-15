// 백엔드 REST API 공통 fetch 래퍼.
//
// 인증 토큰: 실 로그인(OAuth) 연동은 별도 이슈라 "aidrive_token"
// 키는 지금 비어있는 게 정상이다. 로그인이 붙으면 그쪽에서 로그인 성공 시
// localStorage.setItem("aidrive_token", jwt) 만 해주면 이 클라이언트가 자동으로
// Authorization 헤더에 실어 보낸다. 지금은 토큰이 없어 모든 인증 필요 API가 401을
// 받는데, 호출부(폴더 등)에서 실패 시 기존 mock 로직으로 폴백하도록 짜여 있다.
const TOKEN_STORAGE_KEY = "aidrive_token";

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

/**
 * fetch 래퍼. 요청 본문은 JSON으로 직렬화하고, 응답은 JSON으로 파싱한다.
 * 응답 본문이 없는 경우(204 등)를 대비해 빈 응답도 안전하게 처리.
 */
export async function apiFetch<T>(
  path: string,
  options: { method?: string; body?: unknown } = {}
): Promise<T> {
  const token = getAuthToken();

  const response = await fetch(`/api/v1${path}`, {
    method: options.method ?? "GET",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });

  if (!response.ok) {
    // GlobalExceptionHandler는 실제로 {success:false, data:null, error:{code, message}}
    // 형태로 내려준다(ApiResponse.fail). data?.error를 그대로 message로 쓰면 객체가
    // "[object Object]"로 뭉개져서 admin 화면 등에서 에러 사유를 못 보여주는 문제가 있어
    // error.message를 우선 꺼내고, 옛 포맷(error가 문자열)이거나 파싱 실패 시 폴백한다.
    const message = await response
      .json()
      .then((data) => data?.error?.message ?? data?.error ?? response.statusText)
      .catch(() => response.statusText);
    throw new ApiError(response.status, message);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}
