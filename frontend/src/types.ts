// 필드명은 API 문서.md (GET /api/v1/files/{id}, GET /api/v1/users/me/settings) 기준으로 정렬.

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
 */
export interface FileMapping {
  fileId: number;
  targetFolderId: number | null;
  targetTempId: string | null;
  newName: string | null;
}

/** POST /api/v1/organize/propose 응답, POST /api/v1/organize/apply 요청 바디. backend OrganizeProposal과 1:1. */
export interface OrganizeProposal {
  newFolders: ProposedFolder[];
  mappings: FileMapping[];
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
