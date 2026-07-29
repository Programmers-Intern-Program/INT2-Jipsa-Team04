import { createContext } from "react";
import type { OrganizeApplyResponse, OrganizeProposal } from "../types";

export type SmartOrganizeStage =
  | "idle"
  | "uploading"
  | "proposing"
  | "reviewing"
  | "applying"
  | "result"
  | "failed";

export interface SmartOrganizeContextValue {
  stage: SmartOrganizeStage;
  proposal: OrganizeProposal | null;
  applyResult: OrganizeApplyResponse | null;
  error: string | null;
  organizeStep: number;
  isVisible: boolean;
  isUploadFlow: boolean;
  uploadFileIds: number[];
  completedSignal: number;
  startOrganization: (allowRename: boolean) => void;
  startSmartUpload: (sessionId: string, allowRename: boolean) => Promise<void>;
  apply: () => Promise<void>;
  dismiss: () => void;
  show: () => void;
  reset: () => void;
}

export const SmartOrganizeContext = createContext<SmartOrganizeContextValue | null>(null);
