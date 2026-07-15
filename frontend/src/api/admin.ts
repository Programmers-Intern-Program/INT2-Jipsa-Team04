// GET/POST/DELETE/PATCH /api/v1/admin/users — backend/src/main/java/com/jipsa/admin 와 1:1 매칭.
import type { AdminSanction, AdminUser } from "../types";
import { apiFetch } from "./client";

/** GET /api/v1/admin/users — 전체 사용자 목록(페이지네이션). */
export function listAdminUsers(page?: number, size?: number): Promise<{ items: AdminUser[]; total: number }> {
  const params = new URLSearchParams();
  if (page !== undefined) params.set("page", String(page));
  if (size !== undefined) params.set("size", String(size));
  const query = params.toString() ? `?${params.toString()}` : "";
  return apiFetch<{ items: AdminUser[]; total: number }>(`/admin/users${query}`);
}

/** POST /api/v1/admin/users/{id}/suspend — 계정 정지. expiresAt은 ISO 문자열(선택). */
export function suspendUser(
  id: number,
  sanctionType: string,
  reason: string,
  expiresAt?: string
): Promise<void> {
  return apiFetch<{ success: boolean }>(`/admin/users/${id}/suspend`, {
    method: "POST",
    body: { sanctionType, reason, expiresAt: expiresAt || undefined },
  }).then(() => undefined);
}

/** POST /api/v1/admin/users/{id}/unsuspend — 정지 해제. */
export function unsuspendUser(id: number, liftedReason: string): Promise<void> {
  return apiFetch<{ success: boolean }>(`/admin/users/${id}/unsuspend`, {
    method: "POST",
    body: { liftedReason },
  }).then(() => undefined);
}

/** DELETE /api/v1/admin/users/{id} — 관리자에 의한 소프트 삭제. */
export function deleteAdminUser(id: number, reason: string): Promise<void> {
  return apiFetch<{ success: boolean }>(`/admin/users/${id}`, {
    method: "DELETE",
    body: { reason },
  }).then(() => undefined);
}

/** GET /api/v1/admin/users/{id}/sanctions — 제재 이력 조회. */
export function getUserSanctions(id: number): Promise<AdminSanction[]> {
  return apiFetch<{ sanctions: AdminSanction[] }>(`/admin/users/${id}/sanctions`).then((res) => res.sanctions);
}

/** PATCH /api/v1/admin/users/{id}/role — 관리자 권한 부여/해제. role: "ADMIN" | "USERS". */
export function updateUserRole(id: number, role: string): Promise<void> {
  return apiFetch<{ success: boolean }>(`/admin/users/${id}/role`, {
    method: "PATCH",
    body: { role },
  }).then(() => undefined);
}
