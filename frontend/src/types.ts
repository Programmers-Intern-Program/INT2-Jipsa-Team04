// 필드명은 API 문서.md (GET /api/v1/files/{id}, GET /api/v1/users/me/settings) 기준으로 정렬.

/**
 * 공통 응답 envelope. auth/users me 등 백엔드가 ApiResponse로 감싸 내려주는 엔드포인트용.
 * 공용 apiFetch는 이 envelope를 raw로 반환하므로, 호출부(api/auth.ts, api/me.ts)에서
 * .data를 직접 언랩한다. (기존 folders/files/admin/settings 모듈은 raw 응답이라 건드리지 않는다.)
 */
export interface ApiEnvelope<T> {
  success: boolean;
  data: T;
  error: { code: string; message: string } | null;
}

/** POST /api/v1/auth/oauth/google 응답 data. 백엔드 LoginResult와 1:1. */
export interface LoginResult {
  accessToken: string;
  refreshToken: string;
  isNewUser: boolean;
}

/**
 * GET /api/v1/users/me 응답 data. 백엔드 MeResponse와 1:1.
 * role 기본값은 백엔드 DDL 기준 "USERS"(단수 "USER" 아님). email 필드는 백엔드 응답에 없다.
 */
export interface MeResponse {
  userId: number;
  name: string;
  profileImageUrl: string | null;
  role: "USERS" | "ADMIN";
  status: "ACTIVE" | "LOCKED" | "SUSPENDED" | "WITHDRAWN";
}

/**
 * 프론트 세션 사용자. GET /users/me(MeResponse) 기반.
 * email은 백엔드가 내려주지 않으므로 optional이며, 현재는 항상 비어 있다(표시부는 "" 폴백).
 */
export interface SessionUser {
  name: string;
  role: string;
  email?: string;
  userId?: number;
  profileImageUrl?: string | null;
  status?: string;
}

/** GET /api/v1/folders 응답의 폴더 트리 노드. Parent_Folder_IDX 기반 평면 목록, 프론트에서 트리로 조립. */
export interface Folder {
  folderId: number;
  name: string;
  /** null이면 최상위(루트) 폴더. */
  parentFolderId: number | null;
}

export interface Document {
  id: string;
  name: string;
  /** API 응답 스펙엔 없는 필드. RAG 채팅 mock 등 프론트 내부 로직에서만 사용 중 (추후 정리 필요). */
  content: string;
  /** bytes 단위. 화면 표시 시 formatBytes()로 변환. */
  sizeBytes: number;
  /** was: type */
  fileType: string;
  /** was: folder(문자열 경로). Folder.folderId 참조, null이면 미분류(루트). */
  folderId: number | null;
  /** was: aiTags */
  tags: string[];
  /** API 응답 스펙엔 명시되지 않았으나 화면 표시에 필요해 유지. */
  modifiedAt: string;
  /** was: owner */
  ownerName: string;
  /** was: securityLevel */
  securityRank: "일반" | "기밀";
  summary: string;
  piiDetected: boolean;
  status?: string;
  processingStage?: string;
  star?: boolean;
  docType?: string;
  entities?: {
    dates: string[];
    people: string[];
    amounts: string[];
    project: string;
  };
}

export interface AISettings {
  sensitivity: number;
  voiceModel: string;
  responseStyle: "간결형" | "상세형" | "전문가용";
  instantSummary: boolean;
  autoHighlight: boolean;
  pushNotification: boolean;
}

export interface ChatMessage {
  id: string;
  sender: "user" | "ai";
  text: string;
  citations: { name: string; info: string }[];
  timestamp: string;
  processingTime?: string;
  routing?: {
    mode: "lookup" | "synthesis" | "general";
    reasoning: string;
  };
  mapResults?: { docName: string; partialSummary: string }[];
  modelUsed?: string;
}

/** AI 채팅 탭(여러 개의 독립된 대화 창)을 표현. 프론트 전용 개념, 백엔드 스펙엔 없음. */
export interface ChatSession {
  id: string;
  title: string;
  chatHistory: ChatMessage[];
  selectedDocIds: string[];
}

/** GET /api/v1/admin/users 목록 항목. 필드명은 backend AdminUserListItem과 1:1. */
export interface AdminUser {
  userId: number;
  role: string;
  status: "ACTIVE" | "LOCKED" | "SUSPENDED" | "WITHDRAWN" | string;
  del: boolean;
  createdAt: string | null;
  documentCount: number;
  /** Refresh_Tokens 엔티티가 아직 백엔드에 없어 항상 null (별도 이슈). */
  lastLoginAt: string | null;
}

/** GET /api/v1/admin/users/{id}/sanctions 항목. 필드명은 backend SanctionItem과 1:1. */
export interface AdminSanction {
  sanctionType: string;
  sanctionStatus: string;
  reason: string;
  restoreUserStatus: string | null;
  createdAt: string | null;
  expiresAt: string | null;
  liftedByAdminId: number | null;
  liftedAt: string | null;
  liftedReason: string | null;
}
