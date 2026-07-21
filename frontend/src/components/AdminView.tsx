// 관리자 화면 (Req.5~12). backend/src/main/java/com/jipsa/admin의 5개 엔드포인트와 1:1 매칭.
// 이 화면은 role === "ADMIN"인 사용자에게만 App.tsx 사이드바에서 노출된다.
import { useEffect, useState, type ReactNode } from "react";
import { motion } from "motion/react";
import { Ban, History, KeyRound, Loader2, ShieldAlert, Trash2, Undo2 } from "lucide-react";
import type { AdminSanction, AdminUser } from "../types";
import {
  deleteAdminUser,
  getUserSanctions,
  listAdminUsers,
  suspendUser,
  unsuspendUser,
  updateUserRole
} from "../api/admin";
import { getCurrentUserId } from "../api/client";

// suspend 엔드포인트는 실제로 계정을 잠그는(Status=SUSPENDED) 의미가 있는 타입만 받는다.
// WARNING/UPLOAD_LIMIT/LOGIN_BLOCK은 Sanction_Type엔 있어도 "계정 정지"가 아니라서 제외
// (backend AdminService.SUSPENDABLE_TYPES와 동일).
const SANCTION_TYPES = ["TEMP_SUSPEND", "PERMANENT_SUSPEND"] as const;
const PAGE_SIZE = 20;

type ModalState =
  | { type: "suspend"; user: AdminUser }
  | { type: "unsuspend"; user: AdminUser }
  | { type: "delete"; user: AdminUser }
  | { type: "role"; user: AdminUser }
  | { type: "sanctions"; user: AdminUser }
  | null;

function formatDate(value: string | null): string {
  if (!value) return "-";
  try {
    return new Date(value).toLocaleString("ko-KR");
  } catch {
    return value;
  }
}

export default function AdminView() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [isLoading, setIsLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [modal, setModal] = useState<ModalState>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // GET /api/v1/users/me 응답엔 userId가 없어(API 문서.md 1장) JWT sub 클레임에서 직접 뽑아온다.
  // 비로그인 상태(토큰 없음)면 null — 이 경우 "본인" 판별 없이 버튼이 전부 노출되지만,
  // 자기 자신 대상 요청은 어차피 백엔드가 최종적으로 400(SELF_TARGET_NOT_ALLOWED)으로 막는다.
  const currentUserId = getCurrentUserId();

  const loadUsers = async (targetPage: number) => {
    setIsLoading(true);
    setLoadError(null);
    try {
      const res = await listAdminUsers(targetPage, PAGE_SIZE);
      setUsers(res.items);
      setTotal(res.total);
      setPage(targetPage);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "사용자 목록을 불러오지 못했습니다.");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadUsers(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const closeModal = () => {
    setModal(null);
    setActionError(null);
  };

  const runAction = async (action: () => Promise<void>) => {
    setIsSubmitting(true);
    setActionError(null);
    try {
      await action();
      closeModal();
      await loadUsers(page);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "요청 처리 중 오류가 발생했습니다.");
    } finally {
      setIsSubmitting(false);
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <motion.div
      initial={{ opacity: 0, y: 15 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -15 }}
      className="space-y-6 pb-24"
      id="admin-view-wrapper"
    >
      <div id="admin-title-block">
        <h2 className="text-3xl font-bold tracking-tight text-on-surface font-sans flex items-center gap-2">
          <ShieldAlert className="w-7 h-7 text-primary" />
          관리자 — 사용자 관리
        </h2>
        <p className="text-body-md text-on-surface-variant font-sans mt-1">
          전체 사용자 목록을 확인하고 정지/해제/삭제/권한 변경을 처리합니다.
        </p>
      </div>

      <div className="bg-white rounded-3xl border border-outline-variant shadow-sm overflow-hidden" id="admin-user-table-card">
        {loadError && (
          <div className="p-4 text-body-sm text-rose-600 bg-rose-50 border-b border-rose-100">{loadError}</div>
        )}
        <table className="w-full text-body-sm">
          <thead className="bg-surface-container-low text-on-surface-variant text-label-sm font-bold">
            <tr>
              <th className="text-left px-6 py-3">사용자 ID</th>
              <th className="text-left px-6 py-3">권한</th>
              <th className="text-left px-6 py-3">상태</th>
              <th className="text-left px-6 py-3">가입일</th>
              <th className="text-left px-6 py-3">문서 수</th>
              <th className="text-left px-6 py-3">액션</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={6} className="px-6 py-10 text-center text-outline">
                  <Loader2 className="w-5 h-5 animate-spin inline-block mr-2" />
                  불러오는 중...
                </td>
              </tr>
            ) : users.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-6 py-10 text-center text-outline">
                  표시할 사용자가 없습니다.
                </td>
              </tr>
            ) : (
              users.map((u) => {
                const isSelf = currentUserId !== null && u.userId === currentUserId;
                return (
                  <tr key={u.userId} className="border-t border-outline-variant/40 hover:bg-surface-container-low/50">
                    <td className="px-6 py-3 font-semibold text-on-surface">
                      #{u.userId}
                      {isSelf && <span className="ml-1.5 text-[10px] text-primary font-bold align-middle">본인</span>}
                      {u.del && <span className="ml-1.5 text-[10px] text-rose-500 font-bold align-middle">삭제됨</span>}
                    </td>
                    <td className="px-6 py-3">{u.role}</td>
                    <td className="px-6 py-3">
                      <span
                        className={`px-2 py-0.5 rounded-full text-[11px] font-bold ${
                          u.status === "ACTIVE"
                            ? "bg-emerald-50 text-emerald-600"
                            : u.status === "SUSPENDED"
                              ? "bg-amber-50 text-amber-600"
                              : u.status === "WITHDRAWN"
                                ? "bg-rose-50 text-rose-600"
                                : "bg-surface-container text-outline"
                        }`}
                      >
                        {u.status}
                      </span>
                    </td>
                    <td className="px-6 py-3 text-outline">{formatDate(u.createdAt)}</td>
                    <td className="px-6 py-3">{u.documentCount}</td>
                    <td className="px-6 py-3">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <button
                          type="button"
                          onClick={() => setModal({ type: "sanctions", user: u })}
                          className="p-1.5 rounded-lg text-outline hover:bg-surface-container-low hover:text-primary transition-colors cursor-pointer"
                          title="제재 이력"
                        >
                          <History className="w-4 h-4" />
                        </button>
                        {/* 자기 자신 행에는 정지/해제/삭제/권한변경 버튼을 아예 노출하지 않는다.
                            백엔드도 동일 요청을 400(SELF_TARGET_NOT_ALLOWED)으로 막지만,
                            여기서 먼저 막아 UX 상 혼란을 줄인다. */}
                        {!isSelf && (
                          <>
                            <button
                              type="button"
                              onClick={() => setModal({ type: "suspend", user: u })}
                              className="p-1.5 rounded-lg text-outline hover:bg-amber-50 hover:text-amber-600 transition-colors cursor-pointer"
                              title="정지"
                            >
                              <Ban className="w-4 h-4" />
                            </button>
                            <button
                              type="button"
                              onClick={() => setModal({ type: "unsuspend", user: u })}
                              className="p-1.5 rounded-lg text-outline hover:bg-emerald-50 hover:text-emerald-600 transition-colors cursor-pointer"
                              title="정지 해제"
                            >
                              <Undo2 className="w-4 h-4" />
                            </button>
                            <button
                              type="button"
                              onClick={() => setModal({ type: "role", user: u })}
                              className="p-1.5 rounded-lg text-outline hover:bg-surface-container-low hover:text-primary transition-colors cursor-pointer"
                              title="권한 변경"
                            >
                              <KeyRound className="w-4 h-4" />
                            </button>
                            <button
                              type="button"
                              onClick={() => setModal({ type: "delete", user: u })}
                              className="p-1.5 rounded-lg text-outline hover:bg-rose-50 hover:text-rose-600 transition-colors cursor-pointer"
                              title="삭제"
                            >
                              <Trash2 className="w-4 h-4" />
                            </button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>

        <div className="flex items-center justify-between px-6 py-4 border-t border-outline-variant/40 text-body-sm text-outline">
          <span>총 {total}명</span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={page <= 0 || isLoading}
              onClick={() => loadUsers(page - 1)}
              className="px-3 py-1.5 rounded-lg border border-outline-variant disabled:opacity-40 hover:bg-surface-container-low transition-colors cursor-pointer"
            >
              이전
            </button>
            <span>
              {page + 1} / {totalPages}
            </span>
            <button
              type="button"
              disabled={page + 1 >= totalPages || isLoading}
              onClick={() => loadUsers(page + 1)}
              className="px-3 py-1.5 rounded-lg border border-outline-variant disabled:opacity-40 hover:bg-surface-container-low transition-colors cursor-pointer"
            >
              다음
            </button>
          </div>
        </div>
      </div>

      {modal?.type === "suspend" && (
        <SuspendModal
          user={modal.user}
          isSubmitting={isSubmitting}
          error={actionError}
          onClose={closeModal}
          onSubmit={(sanctionType, reason, expiresAt) =>
            runAction(() => suspendUser(modal.user.userId, sanctionType, reason, expiresAt))
          }
        />
      )}
      {modal?.type === "unsuspend" && (
        <ReasonModal
          title="정지 해제"
          description={`#${modal.user.userId} 사용자의 정지를 해제합니다. 해제 사유를 입력하세요.`}
          isSubmitting={isSubmitting}
          error={actionError}
          onClose={closeModal}
          onSubmit={(reason) => runAction(() => unsuspendUser(modal.user.userId, reason))}
        />
      )}
      {modal?.type === "delete" && (
        <ReasonModal
          title="계정 삭제"
          danger
          description={`#${modal.user.userId} 사용자를 소프트 삭제합니다. 삭제 사유를 입력하세요.`}
          isSubmitting={isSubmitting}
          error={actionError}
          onClose={closeModal}
          onSubmit={(reason) => runAction(() => deleteAdminUser(modal.user.userId, reason))}
        />
      )}
      {modal?.type === "role" && (
        <RoleModal
          user={modal.user}
          isSubmitting={isSubmitting}
          error={actionError}
          onClose={closeModal}
          onSubmit={(role) => runAction(() => updateUserRole(modal.user.userId, role))}
        />
      )}
      {modal?.type === "sanctions" && <SanctionsModal user={modal.user} onClose={closeModal} />}
    </motion.div>
  );
}

function ModalShell({
  title,
  danger,
  onClose,
  children
}: {
  title: string;
  danger?: boolean;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <div
      className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center z-[100] p-4"
      onClick={onClose}
    >
      <div className="bg-white rounded-3xl shadow-2xl w-full max-w-md p-6" onClick={(e) => e.stopPropagation()}>
        <h3 className={`text-lg font-bold mb-4 ${danger ? "text-rose-600" : "text-on-surface"}`}>{title}</h3>
        {children}
      </div>
    </div>
  );
}

function SuspendModal({
  user,
  isSubmitting,
  error,
  onClose,
  onSubmit
}: {
  user: AdminUser;
  isSubmitting: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (sanctionType: string, reason: string, expiresAt?: string) => void;
}) {
  const [sanctionType, setSanctionType] = useState<string>("TEMP_SUSPEND");
  const [reason, setReason] = useState("");
  const [expiresAt, setExpiresAt] = useState("");

  return (
    <ModalShell title={`#${user.userId} 계정 정지`} onClose={onClose}>
      <div className="space-y-4">
        <div>
          <label className="text-label-sm font-bold text-on-surface-variant block mb-1">제재 유형</label>
          <select
            value={sanctionType}
            onChange={(e) => setSanctionType(e.target.value)}
            className="w-full border border-outline-variant rounded-xl py-2.5 px-3 text-body-sm"
          >
            {SANCTION_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-label-sm font-bold text-on-surface-variant block mb-1">사유 (필수)</label>
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={3}
            className="w-full border border-outline-variant rounded-xl py-2.5 px-3 text-body-sm"
            placeholder="정지 사유를 입력하세요"
          />
        </div>
        <div>
          <label className="text-label-sm font-bold text-on-surface-variant block mb-1">만료 일시 (선택)</label>
          <input
            type="datetime-local"
            value={expiresAt}
            onChange={(e) => setExpiresAt(e.target.value)}
            className="w-full border border-outline-variant rounded-xl py-2.5 px-3 text-body-sm"
          />
        </div>
        {error && <p className="text-rose-600 text-body-sm">{error}</p>}
        <div className="flex justify-end gap-2 pt-2">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 rounded-xl text-label-md font-bold text-outline hover:bg-surface-container-low cursor-pointer"
          >
            취소
          </button>
          <button
            type="button"
            disabled={isSubmitting || !reason.trim()}
            onClick={() =>
              onSubmit(sanctionType, reason.trim(), expiresAt ? new Date(expiresAt).toISOString() : undefined)
            }
            className="px-4 py-2 rounded-xl text-label-md font-bold text-white bg-amber-600 hover:bg-amber-700 disabled:opacity-50 cursor-pointer"
          >
            {isSubmitting ? "처리 중..." : "정지"}
          </button>
        </div>
      </div>
    </ModalShell>
  );
}

function ReasonModal({
  title,
  description,
  danger,
  isSubmitting,
  error,
  onClose,
  onSubmit
}: {
  title: string;
  description: string;
  danger?: boolean;
  isSubmitting: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (reason: string) => void;
}) {
  const [reason, setReason] = useState("");
  return (
    <ModalShell title={title} danger={danger} onClose={onClose}>
      <div className="space-y-4">
        <p className="text-body-sm text-outline">{description}</p>
        <textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={3}
          className="w-full border border-outline-variant rounded-xl py-2.5 px-3 text-body-sm"
          placeholder="사유를 입력하세요"
        />
        {error && <p className="text-rose-600 text-body-sm">{error}</p>}
        <div className="flex justify-end gap-2 pt-2">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 rounded-xl text-label-md font-bold text-outline hover:bg-surface-container-low cursor-pointer"
          >
            취소
          </button>
          <button
            type="button"
            disabled={isSubmitting || !reason.trim()}
            onClick={() => onSubmit(reason.trim())}
            className={`px-4 py-2 rounded-xl text-label-md font-bold text-white disabled:opacity-50 cursor-pointer ${
              danger ? "bg-rose-600 hover:bg-rose-700" : "bg-primary hover:bg-opacity-90"
            }`}
          >
            {isSubmitting ? "처리 중..." : "확인"}
          </button>
        </div>
      </div>
    </ModalShell>
  );
}

function RoleModal({
  user,
  isSubmitting,
  error,
  onClose,
  onSubmit
}: {
  user: AdminUser;
  isSubmitting: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (role: string) => void;
}) {
  const [role, setRole] = useState(user.role === "ADMIN" ? "USERS" : "ADMIN");
  return (
    <ModalShell title={`#${user.userId} 권한 변경`} onClose={onClose}>
      <div className="space-y-4">
        <p className="text-body-sm text-outline">
          현재 권한: <span className="font-bold text-on-surface">{user.role}</span>
        </p>
        <div className="flex bg-surface-container-low p-1 rounded-xl border border-outline-variant/40">
          {(["USERS", "ADMIN"] as const).map((r) => (
            <button
              key={r}
              type="button"
              onClick={() => setRole(r)}
              className={`flex-1 py-2 text-label-sm rounded-lg font-bold transition-all cursor-pointer ${
                role === r ? "bg-white shadow-sm text-primary" : "text-outline hover:text-on-surface"
              }`}
            >
              {r}
            </button>
          ))}
        </div>
        {error && <p className="text-rose-600 text-body-sm">{error}</p>}
        <div className="flex justify-end gap-2 pt-2">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 rounded-xl text-label-md font-bold text-outline hover:bg-surface-container-low cursor-pointer"
          >
            취소
          </button>
          <button
            type="button"
            disabled={isSubmitting || role === user.role}
            onClick={() => onSubmit(role)}
            className="px-4 py-2 rounded-xl text-label-md font-bold text-white bg-primary hover:bg-opacity-90 disabled:opacity-50 cursor-pointer"
          >
            {isSubmitting ? "처리 중..." : "변경"}
          </button>
        </div>
      </div>
    </ModalShell>
  );
}

function SanctionsModal({ user, onClose }: { user: AdminUser; onClose: () => void }) {
  const [items, setItems] = useState<AdminSanction[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getUserSanctions(user.userId)
      .then(setItems)
      .catch((err) => setError(err instanceof Error ? err.message : "제재 이력을 불러오지 못했습니다."));
  }, [user.userId]);

  return (
    <ModalShell title={`#${user.userId} 제재 이력`} onClose={onClose}>
      <div className="space-y-3 max-h-96 overflow-y-auto">
        {error && <p className="text-rose-600 text-body-sm">{error}</p>}
        {!error && items === null && <p className="text-outline text-body-sm">불러오는 중...</p>}
        {items && items.length === 0 && <p className="text-outline text-body-sm">제재 이력이 없습니다.</p>}
        {items?.map((s, idx) => (
          <div key={idx} className="border border-outline-variant/50 rounded-xl p-3 text-body-sm">
            <div className="flex justify-between items-center mb-1">
              <span className="font-bold text-on-surface">{s.sanctionType}</span>
              <span className="text-[11px] font-bold text-outline">{s.sanctionStatus}</span>
            </div>
            <p className="text-outline mb-1">{s.reason}</p>
            <p className="text-[11px] text-outline">
              {formatDate(s.createdAt)}
              {s.expiresAt ? ` ~ ${formatDate(s.expiresAt)}` : ""}
            </p>
            {s.liftedAt && (
              <p className="text-[11px] text-emerald-600 mt-1">
                해제됨: {formatDate(s.liftedAt)} — {s.liftedReason}
              </p>
            )}
          </div>
        ))}
      </div>
      <div className="flex justify-end pt-4">
        <button
          type="button"
          onClick={onClose}
          className="px-4 py-2 rounded-xl text-label-md font-bold text-outline hover:bg-surface-container-low cursor-pointer"
        >
          닫기
        </button>
      </div>
    </ModalShell>
  );
}
