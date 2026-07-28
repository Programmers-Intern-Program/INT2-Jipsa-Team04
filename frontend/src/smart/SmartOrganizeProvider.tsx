import { useCallback, useState } from "react";
import type { ReactNode } from "react";
import { applyOrganization, proposeForUpload, proposeOrganization } from "../api/organize";
import type { OrganizeApplyResponse, OrganizeProposal } from "../types";
import { SmartOrganizeContext } from "./SmartOrganizeContext";
import type { SmartOrganizeStage } from "./SmartOrganizeContext";
import { useUploads } from "../upload/UploadProvider";

export function SmartOrganizeProvider({ children }: { children: ReactNode }) {
  const [stage, setStage] = useState<SmartOrganizeStage>("idle");
  const [proposal, setProposal] = useState<OrganizeProposal | null>(null);
  const [applyResult, setApplyResult] = useState<OrganizeApplyResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [organizeStep, setOrganizeStep] = useState(0);
  const [isVisible, setIsVisible] = useState(true);
  const [isUploadFlow, setIsUploadFlow] = useState(false);
  const [uploadFileIds, setUploadFileIds] = useState<number[]>([]);
  const [completedSignal, setCompletedSignal] = useState(0);
  const { uploadQueuedAndWait } = useUploads();

  const reset = useCallback(() => {
    setStage("idle");
    setProposal(null);
    setApplyResult(null);
    setError(null);
    setOrganizeStep(0);
    setIsVisible(true);
    setIsUploadFlow(false);
    setUploadFileIds([]);
  }, []);

  const startProposal = useCallback(async (request: () => Promise<OrganizeProposal>, uploadFlow: boolean) => {
    setStage("proposing");
    setProposal(null);
    setApplyResult(null);
    setError(null);
    setOrganizeStep(1);
    setIsVisible(true);
    setIsUploadFlow(uploadFlow);
    const step1 = window.setTimeout(() => setOrganizeStep(2), 800);
    const step2 = window.setTimeout(() => setOrganizeStep(3), 1600);
    try {
      const nextProposal = await request();
      setProposal({ ...nextProposal, idempotencyKey: crypto.randomUUID() });
      setStage("reviewing");
    } catch (err) {
      setStage("failed");
      setError(err instanceof Error ? err.message : "스마트 정리 제안 생성 중 오류가 발생했습니다.");
    } finally {
      window.clearTimeout(step1);
      window.clearTimeout(step2);
    }
  }, []);

  const startOrganization = useCallback((allowRename: boolean) => {
    void startProposal(() => proposeOrganization(allowRename), false);
  }, [startProposal]);

  const startSmartUpload = useCallback(async (allowRename: boolean) => {
    setStage("uploading");
    setProposal(null);
    setApplyResult(null);
    setError(null);
    setIsUploadFlow(true);
    setUploadFileIds([]);
    try {
      const fileIds = await uploadQueuedAndWait();
      setUploadFileIds(fileIds);
      if (fileIds.length === 0) {
        setStage("failed");
        setError("업로드된 파일이 없어 정리를 진행하지 않았습니다.");
        return;
      }
      await startProposal(() => proposeForUpload(fileIds, allowRename), true);
    } catch (err) {
      setStage("failed");
      setError(err instanceof Error ? err.message : "스마트 업로드에 실패했습니다.");
    }
  }, [uploadQueuedAndWait, startProposal]);

  const apply = useCallback(async () => {
    if (!proposal || stage !== "reviewing") return;
    setStage("applying");
    setIsVisible(true);
    setError(null);
    try {
      const result = await applyOrganization(proposal);
      setApplyResult(result);
      setStage("result");
      setCompletedSignal((value) => value + 1);
    } catch (err) {
      setStage("failed");
      setError(err instanceof Error ? err.message : "정리를 적용하는 중 오류가 발생했습니다.");
    }
  }, [proposal, stage]);

  const dismiss = useCallback(() => {
    if (stage === "applying") return;
    if (stage === "proposing" || stage === "reviewing") {
      setIsVisible(false);
      return;
    }
    reset();
  }, [reset, stage]);

  const show = useCallback(() => setIsVisible(true), []);

  return (
    <SmartOrganizeContext.Provider
      value={{
        stage,
        proposal,
        applyResult,
        error,
        organizeStep,
        isVisible,
        isUploadFlow,
        uploadFileIds,
        completedSignal,
        startOrganization,
        startSmartUpload,
        apply,
        dismiss,
        show,
        reset,
      }}
    >
      {children}
    </SmartOrganizeContext.Provider>
  );
}
