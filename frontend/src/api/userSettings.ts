// GET/PATCH /api/v1/users/me/settings — backend/src/main/java/com/jipsa/user 와 1:1 매칭.
import type { AISettings } from "../types";
import { apiFetch } from "./client";

/** GET /api/v1/users/me/settings — 최초 조회 시 서버가 DDL 기본값으로 자동 생성해서 반환한다. */
export function getUserSettings(): Promise<AISettings> {
  return apiFetch<AISettings>("/users/me/settings");
}

/** PATCH /api/v1/users/me/settings — 6개 필드 모두 선택. patch에 없는 키는 "변경 안 함". */
export function updateUserSettings(patch: Partial<AISettings>): Promise<void> {
  return apiFetch<{ success: boolean }>("/users/me/settings", {
    method: "PATCH",
    body: patch,
  }).then(() => undefined);
}
