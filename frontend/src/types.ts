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
  autoMasking: boolean;
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
