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
  documentType?: string | null;
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

/** GET /api/v1/organize/current-tree, propose/apply 공용 폴더 트리 노드. backend FolderTreeNode와 1:1. */
export interface OrganizeFolderTreeNode {
  folderId: number;
  name: string;
  children: OrganizeFolderTreeNode[];
}

/**
 * AI가 제안한, 아직 실제로 존재하지 않는 새 폴더. backend ProposedFolder와 1:1.
 * parentTempId/parentFolderId는 동시에 채워지지 않음(정확히 하나만 사용).
 */
export interface ProposedFolder {
  tempId: string;
  name: string;
  parentTempId: string | null;
  parentFolderId: number | null;
}

/**
 * 파일 하나를 어디로 옮기고 어떤 이름으로 바꿀지에 대한 AI 제안. backend FileMapping과 1:1.
 * targetFolderId/targetTempId 동시 사용 불가, 둘 다 null이면 루트로 이동.
 * confidence: 이 매핑에 대한 AI 확신도(0~1). apply 시 사용자의 자동 분류 민감도보다 낮으면
 * 자동 반영에서 제외되고 OrganizeApplyResponse.held로 돌아온다. 없을 수 있다(optional).
 */
export interface FileMapping {
  fileId: number;
  targetFolderId: number | null;
  targetTempId: string | null;
  newName: string | null;
  confidence?: number | null;
}

/** POST /api/v1/organize/propose 응답, POST /api/v1/organize/apply 요청 바디. backend OrganizeProposal과 1:1. */
export interface OrganizeProposal {
  newFolders: ProposedFolder[];
  mappings: FileMapping[];
  /**
   * apply 요청에만 실어 보내는 재시도 방지용 키(propose 응답엔 없음, undefined).
   * 같은 승인 동작을 재시도할 때 항상 같은 값을 보내야 서버가 중복 반영을 막을 수 있다 —
   * handleApplyOrganization에서 제안을 받을 때 한 번만 생성해 organizeResult에 붙여둔다.
   */
  idempotencyKey?: string;
}

/**
 * POST /api/v1/organize/apply 응답. backend OrganizeApplyResponse와 1:1.
 * held: confidence가 사용자의 자동 분류 민감도보다 낮아 자동 반영에서 제외되고 보류된 매핑 목록.
 * 이 목록의 파일은 이동/이름변경되지 않고 원래 위치에 그대로 남는다.
 */
export interface OrganizeApplyResponse {
  success: boolean;
  held: FileMapping[];
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
