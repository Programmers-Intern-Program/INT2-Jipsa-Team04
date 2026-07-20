// 내 정보 API 래퍼. 백엔드 UserProfileController(GET /api/v1/users/me)와 1:1.
//
// auth와 마찬가지로 ApiResponse<MeResponse> envelope로 내려오므로 .data를 언랩한다.
// 인증 필요 엔드포인트라 apiFetch가 localStorage의 aidrive_token을 Authorization 헤더에 실어 보낸다.
import type { ApiEnvelope, MeResponse } from "../types";
import { apiFetch } from "./client";

/** GET /api/v1/users/me — 현재 로그인한 사용자의 userId·name·profileImageUrl·role·status. */
export async function getMe(): Promise<MeResponse> {
  const res = await apiFetch<ApiEnvelope<MeResponse>>("/users/me");
  return res.data;
}
