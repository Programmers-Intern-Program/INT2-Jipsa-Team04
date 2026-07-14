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
    // GlobalExceptionHandler가 {error: string} 형태로 내려주지만, 파싱 실패에도 대비.
    const message = await response
      .json()
      .then((data) => data?.error ?? response.statusText)
      .catch(() => response.statusText);
    throw new ApiError(response.status, message);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}
