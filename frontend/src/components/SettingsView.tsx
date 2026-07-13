import React, { useState, useEffect } from "react";
import { motion, AnimatePresence } from "motion/react";
import { 
  Edit, 
  Laptop, 
  HardDrive, 
  Bell, 
  ChevronDown, 
  Zap
} from "lucide-react";
import type { AISettings } from "../types";

interface SettingsViewProps {
  user: { name: string; email: string; role: string } | null;
  committedSettings: AISettings;
  onSaveSettings: (settings: AISettings) => Promise<void>;
}

export default function SettingsView({ user, committedSettings, onSaveSettings }: SettingsViewProps) {
  const [localSettings, setLocalSettings] = useState<AISettings>({ ...committedSettings });
  const [isSaving, setIsSaving] = useState(false);

  // Sync state if committedSettings changes from parent
  useEffect(() => {
    setLocalSettings({ ...committedSettings });
  }, [committedSettings]);

  // Check if there are unsaved changes
  const hasChanges = JSON.stringify(localSettings) !== JSON.stringify(committedSettings);

  const handleSliderChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setLocalSettings({
      ...localSettings,
      sensitivity: parseFloat(e.target.value) / 100
    });
  };

  const handleToggle = (key: keyof AISettings) => {
    setLocalSettings({
      ...localSettings,
      [key]: !localSettings[key]
    });
  };

  const handleResponseStyleChange = (style: "간결형" | "상세형" | "전문가용") => {
    setLocalSettings({
      ...localSettings,
      responseStyle: style
    });
  };

  const handleVoiceModelChange = (model: string) => {
    setLocalSettings({
      ...localSettings,
      voiceModel: model
    });
  };

  const handleSave = async () => {
    setIsSaving(true);
    try {
      await onSaveSettings(localSettings);
      alert("환경 설정 변경 사항이 성공적으로 저장되었습니다!");
    } catch (err) {
      alert("설정 저장 중 오류가 발생했습니다.");
    } finally {
      setIsSaving(false);
    }
  };

  const handleCancel = () => {
    setLocalSettings({ ...committedSettings });
  };

  const getSensitivityLabel = (val: number) => {
    if (val > 0.8) return `높음 (${val.toFixed(2)})`;
    if (val < 0.4) return `낮음 (${val.toFixed(2)})`;
    return `중간 (${val.toFixed(2)})`;
  };

  return (
    <motion.div 
      initial={{ opacity: 0, y: 15 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -15 }}
      className="space-y-8 pb-24"
      id="settings-view-wrapper"
    >
      {/* Title */}
      <div id="settings-title-block">
        <h2 className="text-3xl font-bold tracking-tight text-on-surface font-sans">계정 및 환경 설정</h2>
        <p className="text-body-md text-on-surface-variant font-sans mt-1">AI Drive의 클라우드 경험을 개인화하고 작업 효율을 한층 더 높이세요.</p>
      </div>

      {/* Bento Grid Settings Layout */}
      <div className="grid grid-cols-12 gap-6" id="settings-bento-grid">
        
        {/* Left column (Profile & Storage, span 4) */}
        <section className="col-span-12 lg:col-span-4 flex flex-col gap-6" id="settings-left-col">
          {/* Profile Card */}
          <div className="bg-white/80 backdrop-blur-md p-6 rounded-3xl border border-outline-variant shadow-sm flex flex-col items-center text-center relative overflow-hidden" id="card-settings-profile">
            <div className="absolute right-0 top-0 w-24 h-24 bg-primary/5 blur-2xl rounded-full"></div>
            <div className="relative mb-4">
              <img 
                src="https://lh3.googleusercontent.com/aida-public/AB6AXuCKKIn2elQpF3hFbise3_TUdXNOQBNTP3fcCXcf-RP4YOiJ-n3tU3buoUJ3eGBRBkEpzAeBlez5zzQOEs-SsOhHMALfLrtuFeVj3s4-hhGG4wCNmAOdpawgvAcyupp4KQcadGBbf3K4g2e0OspQNdzzC-E3ZDYm8zQpRLCcu5hQOBN8sU_B_AkxF2WkiAulSglvNZ-0nTpU53f1jNc9Z1xM1DEU_GvRZrhvj9w5OdJwYkuuOEmYcvwqlg" 
                alt={`${user?.name || "사용자"} 프로필`}
                className="w-32 h-32 rounded-full object-cover border-4 border-white shadow-xl"
              />
              <button
                type="button"
                onClick={() => alert("현재 프로필 변경 사진 업로드는 지원 대기 중입니다.")}
                className="absolute bottom-1 right-1 bg-primary text-white p-2 rounded-full shadow-lg hover:scale-105 transition-transform cursor-pointer"
                title="프로필 편집"
              >
                <Edit className="w-4.5 h-4.5" />
              </button>
            </div>
            <h3 className="text-xl font-bold text-on-surface font-sans">{user?.name || "사용자"}</h3>
            <p className="text-on-surface-variant text-body-sm font-medium mt-0.5">{user?.email || ""}</p>
            
            <div className="flex gap-2 w-full mt-5">
              <button 
                type="button" 
                onClick={() => alert("개인 정보 및 비밀번호 수정 모듈로 진입합니다.")}
                className="flex-1 bg-surface-container-high py-2.5 rounded-xl text-label-md font-bold text-primary hover:bg-surface-container-highest transition-colors cursor-pointer border border-outline-variant/30"
              >
                프로필 관리
              </button>
            </div>
          </div>

          {/* Storage Visualization Card */}
          <div className="bg-white/80 backdrop-blur-md p-6 rounded-3xl border border-outline-variant shadow-sm" id="card-settings-storage">
            <div className="flex justify-between items-center mb-6">
              <h4 className="font-bold text-label-md text-on-surface flex items-center gap-1.5">
                <HardDrive className="w-5 h-5 text-primary" />
                스토리지 사용량
              </h4>
              <span className="text-primary font-bold text-xs bg-primary/5 px-2.5 py-1 rounded-full">82% 사용 중</span>
            </div>

            {/* Horizontal Stacked Progress Bar */}
            <div className="h-3 w-full bg-surface-container rounded-full overflow-hidden mb-4 flex">
              <div className="h-full bg-primary" style={{ width: "42%" }} title="문서 41.2 GB"></div>
              <div className="h-full bg-secondary" style={{ width: "40%" }} title="미디어 38.5 GB"></div>
              <div className="h-full bg-outline-variant" style={{ width: "18%" }} title="여유 공간"></div>
            </div>

            <div className="space-y-3" id="storage-breakdown">
              <div className="flex justify-between items-center text-body-sm">
                <span className="flex items-center gap-2 text-outline font-semibold">
                  <span className="w-2.5 h-2.5 rounded-full bg-primary"></span> 문서
                </span>
                <span className="font-bold text-on-surface">41.2 GB</span>
              </div>
              <div className="flex justify-between items-center text-body-sm">
                <span className="flex items-center gap-2 text-outline font-semibold">
                  <span className="w-2.5 h-2.5 rounded-full bg-secondary"></span> 미디어
                </span>
                <span className="font-bold text-on-surface">38.5 GB</span>
              </div>
              <div className="flex justify-between items-center text-body-sm">
                <span className="flex items-center gap-2 text-outline font-semibold">
                  <span className="w-2.5 h-2.5 rounded-full bg-outline-variant"></span> 여유 공간
                </span>
                <span className="font-bold text-on-surface">20.3 GB</span>
              </div>
            </div>

            <button 
              type="button"
              onClick={() => alert("스토리지 업그레이드 결제 창으로 이동합니다.")}
              className="w-full mt-6 py-3 border border-primary text-primary rounded-xl font-bold text-label-md hover:bg-primary/5 transition-all cursor-pointer"
            >
              용량 업그레이드
            </button>
          </div>
        </section>

        {/* Right column (AI preferences, span 8) */}
        <section className="col-span-12 lg:col-span-8 flex flex-col gap-6" id="settings-right-col">
          {/* AI Settings Box */}
          <div className="bg-white/80 backdrop-blur-md p-8 rounded-3xl border border-outline-variant shadow-sm relative overflow-hidden" id="card-ai-env-configs">
            <div className="absolute top-0 right-0 w-64 h-64 bg-secondary/5 blur-[80px] -z-10 rounded-full pointer-events-none"></div>
            
            <div className="flex items-center gap-3 mb-8">
              <Zap className="w-7 h-7 text-secondary fill-secondary/10 shrink-0" />
              <h3 className="font-bold text-2xl text-on-surface font-sans">AI 환경 설정</h3>
            </div>

            <div className="space-y-8">
              {/* Classification Threshold Range */}
              <div className="flex flex-col gap-4">
                <div className="flex justify-between items-end">
                  <div>
                    <label className="font-bold text-label-md text-on-surface block mb-1">자동 분류 민감도</label>
                    <p className="text-body-sm text-outline">문서를 폴더로 자동 분류하여 정렬할 때의 AI 정확도 임계값을 조정합니다.</p>
                  </div>
                  <span className="text-secondary font-extrabold text-label-md" id="sensitivity-value-indicator">
                    {getSensitivityLabel(localSettings.sensitivity)}
                  </span>
                </div>
                <input 
                  type="range"
                  min="0"
                  max="100"
                  value={Math.round(localSettings.sensitivity * 100)}
                  onChange={handleSliderChange}
                  className="w-full h-2 bg-surface-container rounded-full appearance-none cursor-pointer accent-secondary"
                  id="sensitivity-range-slider"
                />
              </div>

              {/* Voice model & Response Style Grid */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6 pt-4 border-t border-outline-variant/30">
                {/* Voice Model Selector */}
                <div className="flex flex-col gap-2">
                  <label className="font-bold text-label-md text-on-surface">AI 음성 모델</label>
                  <div className="relative">
                    <select 
                      value={localSettings.voiceModel}
                      onChange={(e) => handleVoiceModelChange(e.target.value)}
                      className="w-full bg-white border border-outline-variant rounded-xl py-3 px-4 appearance-none focus:ring-2 focus:ring-secondary/20 focus:border-secondary outline-none transition-all cursor-pointer font-semibold text-body-sm text-on-surface"
                    >
                      <option value="Nova (명확하고 신뢰감 있는)">Nova (명확하고 신뢰감 있는)</option>
                      <option value="Echo (부드럽고 차분한)">Echo (부드럽고 차분한)</option>
                      <option value="Shimmer (지적이고 빠른)">Shimmer (지적이고 빠른)</option>
                      <option value="Onyx (깊고 안정적인)">Onyx (깊고 안정적인)</option>
                    </select>
                    <span className="absolute right-4 top-1/2 -translate-y-1/2 pointer-events-none text-outline">
                      <ChevronDown className="w-4 h-4" />
                    </span>
                  </div>
                </div>

                {/* Response Style tabs */}
                <div className="flex flex-col gap-2">
                  <label className="font-bold text-label-md text-on-surface">응답 스타일</label>
                  <div className="flex bg-surface-container-low p-1 rounded-xl border border-outline-variant/40">
                    {(["간결형", "상세형", "전문가용"] as const).map((style) => (
                      <button 
                        key={style}
                        type="button"
                        onClick={() => handleResponseStyleChange(style)}
                        className={`flex-1 py-2 text-label-sm rounded-lg font-bold transition-all cursor-pointer ${
                          localSettings.responseStyle === style 
                            ? "bg-white shadow-sm text-primary" 
                            : "text-outline hover:text-on-surface"
                        }`}
                      >
                        {style}
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              {/* AI Functional Toggles */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-x-12 gap-y-6 pt-6 border-t border-outline-variant/30">
                {/* Immediate Summary */}
                <div className="flex justify-between items-center gap-4">
                  <div>
                    <p className="font-bold text-label-md text-on-surface">문서 즉시 요약</p>
                    <p className="text-xs text-outline mt-0.5">업로드 즉시 AI가 파일의 3줄 요약본을 자동 생성합니다.</p>
                  </div>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input 
                      type="checkbox"
                      checked={localSettings.instantSummary}
                      onChange={() => handleToggle("instantSummary")}
                      className="sr-only peer"
                    />
                    <div className="w-11 h-6 bg-outline-variant/40 rounded-full peer peer-focus:ring-2 peer-focus:ring-secondary/20 peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-secondary"></div>
                  </label>
                </div>

                {/* Auto PII Masking */}
                <div className="flex justify-between items-center gap-4">
                  <div>
                    <p className="font-bold text-label-md text-on-surface">개인정보 자동 마스킹</p>
                    <p className="text-xs text-outline mt-0.5">민감 인적 정보를 식별하여 문서를 자동으로 검열합니다.</p>
                  </div>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input 
                      type="checkbox"
                      checked={localSettings.autoMasking}
                      onChange={() => handleToggle("autoMasking")}
                      className="sr-only peer"
                    />
                    <div className="w-11 h-6 bg-outline-variant/40 rounded-full peer peer-focus:ring-2 peer-focus:ring-secondary/20 peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-secondary"></div>
                  </label>
                </div>
              </div>
            </div>
          </div>

          {/* Notifications Card */}
          <div className="bg-white/80 backdrop-blur-md p-8 rounded-3xl border border-outline-variant shadow-sm" id="card-notification-settings">
            <h3 className="font-bold text-xl text-on-surface mb-6 flex items-center gap-3">
              <Bell className="w-5 h-5 text-primary" />
              알림 설정
            </h3>
            
            <div className="space-y-4">
              {/* Browser Push */}
              <div className="flex items-center justify-between p-4 rounded-2xl hover:bg-surface-container-low transition-colors group">
                <div className="flex items-center gap-4">
                  <div className="w-10 h-10 rounded-full bg-secondary/10 text-secondary flex items-center justify-center shrink-0">
                    <Laptop className="w-5 h-5" />
                  </div>
                  <div>
                    <p className="font-bold text-label-md text-on-surface">브라우저 푸시 알림</p>
                    <p className="text-xs text-outline mt-0.5">대용량 파일 분석 완료 및 중요 AI 권장 사항이 들어오는 즉시 브라우저 알림을 받습니다.</p>
                  </div>
                </div>
                <label className="relative inline-flex items-center cursor-pointer">
                  <input 
                    type="checkbox"
                    checked={localSettings.pushNotification}
                    onChange={() => handleToggle("pushNotification")}
                    className="sr-only peer"
                  />
                  <div className="w-11 h-6 bg-outline-variant/40 rounded-full peer peer-focus:ring-2 peer-focus:ring-primary/20 peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-primary"></div>
                </label>
              </div>
            </div>
          </div>
        </section>
      </div>

      {/* Floating Save Action Bar (animates in when changes exist) */}
      <AnimatePresence>
        {hasChanges && (
          <motion.div 
            initial={{ y: 50, x: "-50%", opacity: 0 }}
            animate={{ y: 0, x: "-50%", opacity: 1 }}
            exit={{ y: 50, x: "-50%", opacity: 0 }}
            className="fixed bottom-10 left-1/2 -translate-x-1/2 flex items-center gap-4 bg-on-surface/95 backdrop-blur-xl text-white px-8 py-4 rounded-full shadow-2xl z-[90] border border-white/10"
            id="floating-save-bar"
          >
            <p className="flex items-center gap-2 text-body-sm font-semibold font-sans">
              <span className="w-2.5 h-2.5 rounded-full bg-secondary animate-pulse"></span>
              변경사항이 감지되었습니다.
            </p>
            <div className="w-[1px] bg-white/20 h-5 my-auto"></div>
            <div className="flex gap-4">
              <button 
                type="button"
                onClick={handleCancel}
                className="text-label-md font-bold text-white/70 hover:text-white transition-colors cursor-pointer"
              >
                취소
              </button>
              <button 
                type="button"
                disabled={isSaving}
                onClick={handleSave}
                className="bg-secondary text-white px-6 py-2 rounded-full font-bold text-label-md hover:bg-secondary-container hover:text-on-secondary-container transition-all cursor-pointer shadow-lg shadow-secondary/10 flex items-center gap-1.5"
              >
                {isSaving ? (
                  <div className="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin"></div>
                ) : (
                  "변경사항 저장"
                )}
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}
