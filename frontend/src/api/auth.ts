// 인증(Google OAuth) API 래퍼. 백엔드 AuthController(/api/v1/auth/*)와 1:1.
//
// auth 엔드포인트는 공통 응답 규칙에 따라 ApiResponse<T> envelope로 내려온다
// ({ success, data, error }). 공용 apiFetch는 envelope를 raw로 반환하므로 여기서 .data를
// 직접 언랩한다. (기존 folders/files/admin/settings 모듈은 raw 응답이라 언랩하지 않는다.)
import type { ApiEnvelope, LoginResult } from "../types";
import { apiFetch } from "./client";

/**
 * POST /api/v1/auth/oauth/google — Google authorization code로 로그인(최초 시 계정 자동 생성).
 * 토큰 발급 전 호출이라 인증 헤더 없이 열려 있다.
 *
 * @param authorizationCode Google에서 받은 authorization code
 * @param codeVerifier PKCE 원문 verifier (로그인 시작 시 생성해 sessionStorage에 보관하던 값).
 *                     백엔드가 Google 토큰 교환의 code_verifier로 전달해 code를 이번 로그인 시도에 묶는다.
 * @returns accessToken · refreshToken · isNewUser
 */
export async function loginWithGoogle(
  authorizationCode: string,
  codeVerifier: string
): Promise<LoginResult> {
  const res = await apiFetch<ApiEnvelope<LoginResult>>("/auth/oauth/google", {
    method: "POST",
    body: { authorizationCode, codeVerifier },
  });
  return res.data;
}

/**
 * POST /api/v1/auth/logout — Refresh Token 폐기(로그아웃).
 * 위조/없는 토큰은 401을 던질 수 있으므로 호출부에서 best-effort로 감싸 실패를 무시한다.
 *
 * @param refreshToken 보관 중인 Refresh Token 원문
 */
export async function logout(refreshToken: string): Promise<void> {
  await apiFetch("/auth/logout", {
    method: "POST",
    body: { refreshToken },
  });
}
