import React, { useState, useMemo, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "motion/react";
import { 
  Grid, 
  List, 
  Search, 
  Folder, 
  FolderClosed, 
  FileText, 
  FileSpreadsheet, 
  Sparkles, 
  ChevronRight, 
  ChevronDown,
  Download, 
  Plus, 
  X, 
  Upload, 
  Check, 
  HardDrive,
  FolderPlus,
  FolderInput,
  Star,
  Clock,
  Trash2,
  ShieldAlert,
  Undo2,
  Pencil,
  Info
} from "lucide-react";
import type { Document, FileMapping, Folder as FolderType, OrganizeApplyResponse, OrganizeProposal, ProposedFolder } from "../types";
import { formatBytes } from "../utils/formatBytes";
import { formatDateTime } from "../utils/formatDateTime";
import { fetchWithRetry } from "../utils/retry";
import { getFolderPath, getFolderAncestors, isDescendantOrSelf } from "../utils/folderTree";
import {
  listFolders,
  createFolder,
  deleteFolder,
  updateFolder,
  listAllFolderTrash,
  restoreFolder,
  permanentDeleteFolder,
} from "../api/folders";
import { deleteFile, downloadFile, getDocumentTypes, getFileDetail, getFileStatus, getStorageUsage, listAllFiles, listAllTrash, listFiles, moveFiles, permanentDeleteFile, renameFile, restoreFile, toDocument, toggleStar } from "../api/files";
import { proposeOrganization, applyOrganization } from "../api/organize";
import { ApiError } from "../api/client";
import FileDetailPanel from "./FileDetailPanel";
import { uploadFiles, getUploadStatus } from "../api/uploads";
import { useUploads } from "../upload/UploadProvider";

interface MyDocumentsViewProps {
  documents: Document[];
  onUploadDocument: (doc: { name: string; content: string; type: string }) => Promise<void>;
  onNavigateToChat: (docIds: string[]) => void;
  isUploadOpen: boolean;
  setIsUploadOpen: (open: boolean) => void;
  onUpdateDocuments: React.Dispatch<React.SetStateAction<Document[]>>;
  /** 스마트 정리 미리보기에서 confidence 미달 매핑/폴더를 미리 가려서 보여주는 데 쓰는 사용자의
   * 자동 분류 민감도(0~1). 실제 필터링은 서버(OrganizeService)가 하고, 여기선 그 결과를 미리
   * 추측해 보여주는 용도라 서버 판단과 100% 같다는 보장은 없다(둘 다 같은 규칙을 쓰긴 함). */
  sensitivity: number;
}

export default function MyDocumentsView({
  documents,
  onNavigateToChat,
  isUploadOpen,
  setIsUploadOpen,
  onUpdateDocuments,
  sensitivity
}: MyDocumentsViewProps) {
  const [viewMode, setViewMode] = useState<"grid" | "list">("grid");
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedFolder, setSelectedFolder] = useState<number | null>(null);
  const [selectedType, setSelectedType] = useState<string | null>(null);
  const [selectedSecurity, setSelectedSecurity] = useState<string | null>(null);
  const [selectedDocumentType, setSelectedDocumentType] = useState<string | null>(null);
  const [tagFilter, setTagFilter] = useState("");
  const [dateFromFilter, setDateFromFilter] = useState("");
  const [dateToFilter, setDateToFilter] = useState("");
  const [documentTypeOptions, setDocumentTypeOptions] = useState<string[]>([]);
  useEffect(() => {
    let cancelled = false;
    getDocumentTypes().then((types) => { if (!cancelled) setDocumentTypeOptions(types); }).catch(() => {});
    return () => { cancelled = true; };
  }, []);
  const [serverSearchDocs, setServerSearchDocs] = useState<Document[] | null>(null);
  const [searchTotal, setSearchTotal] = useState(0);
  const [searchSize, setSearchSize] = useState(20);
  const [searchPage, setSearchPage] = useState(0);
  const [trashDocs, setTrashDocs] = useState<Document[] | null>(null);
  const [trashFolders, setTrashFolders] = useState<FolderType[] | null>(null);
  const [storage, setStorage] = useState<{ usedBytes: number; quotaBytes: number } | null>(null);

  // AI Organize state
  const [isSmartMenuOpen, setIsSmartMenuOpen] = useState(false);
  const [isOrganizing, setIsOrganizing] = useState(false);
  const [organizeStep, setOrganizeStep] = useState(0);
  const [organizeResult, setOrganizeResult] = useState<OrganizeProposal | null>(null);
  const [isApplyingOrganize, setIsApplyingOrganize] = useState(false);
  // apply 응답(성공 여부 + held 목록) — null이면 아직 미리보기 단계(적용 전), 값이 있으면
  // 모달이 "결과" 화면으로 전환된다. 알림창(alert)이 아니라 모달 안에서 결과를 보여주기 위함.
  const [applyResult, setApplyResult] = useState<OrganizeApplyResponse | null>(null);

  // AI Smart Upload specialized state
  const [isSpecialUploadMode, setIsSpecialUploadMode] = useState(false);
  const [showAutoResultModal, setShowAutoResultModal] = useState(false);
  const [autoResultData, setAutoResultData] = useState<{
    fileName: string;
    targetFolder: string;
    targetFolderId: number | null;
    summary: string;
    tags: string[];
    security: string;
  } | null>(null);

  // New document form state
  const [uploadName, setUploadName] = useState("");
  const [uploadContent, setUploadContent] = useState("");
  const [isUploading, setIsUploading] = useState(false);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [isNewUploadOpen, setIsNewUploadOpen] = useState(false);
  const { items: uploadQueue, isBusy: isNewUploaderBusy, enqueue, startAll, removeItem: removeUploadQueueItem, retryItem, refreshRecent } = useUploads();

  useEffect(() => {
    if (!isNewUploadOpen) return;
    refreshRecent();
    const timer = setInterval(refreshRecent, 5000);
    return () => clearInterval(timer);
  }, [isNewUploadOpen, refreshRecent]);
  const [newUploaderDragActive, setNewUploaderDragActive] = useState(false);
  const newUploaderInputRef = useRef<HTMLInputElement>(null);
  const [dragActive, setDragActive] = useState(false);

  const starPendingRef = useRef<Set<string>>(new Set());

  // 이 컴포넌트는 App.tsx가 user 없이는 렌더링 자체를 안 하므로(LandingView로 대체),
  // 여기서 listFolders()가 실패하는 건 "비로그인"이 아니라 진짜 네트워크/백엔드 오류다.
  // 로그인된 사용자에게 실제 폴더 대신 mock 데이터를 보여주는 건 오히려 혼란만 주므로,
  // 초기값은 빈 배열로 두고 실패하면 mock으로 가리지 않고 재시도(fetchWithRetry)한다.
  // localStorage 캐시는 이전에 실제로 성공한 조회 결과만 저장되므로(아래 별도 effect),
  // 재시도 중 빠른 첫 화면 표시용으로만 쓴다.
  const [folders, setFolders] = useState<FolderType[]>(() => {
    const saved = localStorage.getItem("aidrive_folders");
    return saved ? JSON.parse(saved) : [];
  });

  useEffect(() => {
    fetchWithRetry(listFolders)
      .then(setFolders)
      .catch((err) => {
        console.error("[folders] GET /api/v1/folders 재시도 후에도 실패:", err);
      });
  }, []);

  // Save folders to localStorage on change
  useEffect(() => {
    localStorage.setItem("aidrive_folders", JSON.stringify(folders));
  }, [folders]);

  const createFolderPathViaApi = async (
    currentFolders: FolderType[],
    segments: string[],
    startParentId: number | null = null
  ): Promise<{ folders: FolderType[]; leafId: number | null }> => {
    let working = [...currentFolders];
    let parentId: number | null = startParentId;
    let leafId: number | null = null;

    for (const rawName of segments) {
      const name = rawName.trim();
      if (!name) continue;

      const existing = working.find((f) => f.parentFolderId === parentId && f.name === name);
      if (existing) {
        parentId = existing.folderId;
        leafId = existing.folderId;
        continue;
      }

      const newId = await createFolder(name, parentId);
      working = [...working, { folderId: newId, name, parentFolderId: parentId }];
      parentId = newId;
      leafId = newId;
    }

    return { folders: working, leafId };
  };

  // Folder collapse state
  const [expandedFolders, setExpandedFolders] = useState<Record<number, boolean>>({});

  // Google Drive Mimicry States
  const [currentTab, setCurrentTab] = useState<"mydrive" | "starred" | "secure" | "recent" | "trash">("mydrive");
  const [isMyDriveExpanded, setIsMyDriveExpanded] = useState(true);

  // Document checkbox selection state for batch actions
  const [checkedDocIds, setCheckedDocIds] = useState<string[]>([]);
  const [detailFileId, setDetailFileId] = useState<number | null>(null);

  // Modals state
  const [isNewFolderModalOpen, setIsNewFolderModalOpen] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");
  const [isMoveModalOpen, setIsMoveModalOpen] = useState(false);
  const [movingDocIds, setMovingDocIds] = useState<string[]>([]);
  const [moveTargetFolder, setMoveTargetFolder] = useState<number | null>(null);
  const [isCreatingNewFolderInMove, setIsCreatingNewFolderInMove] = useState(false);
  const [newFolderNameInMove, setNewFolderNameInMove] = useState("");
  const [movingFolderId, setMovingFolderId] = useState<number | null>(null);
  const [folderMoveTarget, setFolderMoveTarget] = useState<number | null>(null);

  useEffect(() => {
    setSearchPage(0);
  }, [searchQuery, selectedType]);

  useEffect(() => {
    const keyword = searchQuery.trim();
    if (!keyword || currentTab !== "mydrive") {
      setServerSearchDocs(null);
      setSearchTotal(0);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      listFiles({ keyword, docType: selectedType ?? undefined, page: searchPage })
          .then((res) => {
            if (cancelled) return;
            setServerSearchDocs(res.items.map(toDocument));
            setSearchTotal(res.total);
            setSearchSize(res.size);
          })
          .catch((err) => {
            if (cancelled) return;
            console.warn("[files] GET /api/v1/files 실패 - mock 데이터로 폴백(비로그인 상태면 정상):", err);
            setServerSearchDocs(null);
            setSearchTotal(0);
          });
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [searchQuery, selectedType, searchPage, currentTab]);

  useEffect(() => {
    if (currentTab !== "trash") {
      setTrashDocs(null);
      setTrashFolders(null);
      return;
    }
    let cancelled = false;
    listAllTrash()
        .then((docs) => {
          if (cancelled) return;
          setTrashDocs(docs);
        })
        .catch((err) => {
          if (cancelled) return;
          console.warn("[files] GET /api/v1/files/trash 실패 - 빈 목록 표시(비로그인 상태면 정상):", err);
          setTrashDocs([]);
        });
    listAllFolderTrash()
        .then((folders) => {
          if (cancelled) return;
          setTrashFolders(folders);
        })
        .catch((err) => {
          if (cancelled) return;
          console.warn("[folders] GET /api/v1/folders/trash 실패 - 빈 목록 표시(비로그인 상태면 정상):", err);
          setTrashFolders([]);
        });
    return () => {
      cancelled = true;
    };
  }, [currentTab]);

  useEffect(() => {
    getStorageUsage()
        .then(setStorage)
        .catch((err) => {
          console.warn("[files] GET /api/v1/files/storage 실패 - 사용량 표시 보류(비로그인 상태면 정상):", err);
          setStorage(null);
        });
  }, []);

  useEffect(() => {
    const pending = documents.filter((d) => d.status === "PROCESSING" || d.status === "UPLOADED");
    if (pending.length === 0) return;
    let cancelled = false;
    const timer = setInterval(async () => {
      const results = await Promise.allSettled(
          pending.map((d) => getFileStatus(Number(d.id)).then((s) => ({ id: d.id, status: s.status })))
      );
      if (cancelled) return;
      const done = results
          .filter((r): r is PromiseFulfilledResult<{ id: string; status: string }> => r.status === "fulfilled")
          .map((r) => r.value)
          .filter((u) => u.status !== "PROCESSING" && u.status !== "UPLOADED");
      if (done.length > 0) {
        onUpdateDocuments(
            documents.map((d) => {
              const u = done.find((x) => x.id === d.id);
              return u ? { ...d, status: u.status } : d;
            })
        );
      }
    }, 4000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [documents, onUpdateDocuments]);

  const storagePercent = storage && storage.quotaBytes > 0
      ? Math.min(100, Math.round((storage.usedBytes / storage.quotaBytes) * 100))
      : 0;

  // Filter logic: match exact folder or nested sub-folder
  const filteredDocuments = useMemo(() => {
    const source = currentTab === "trash"
        ? (trashDocs ?? [])
        : (serverSearchDocs ?? documents);
    return source.filter((doc) => {
      const matchesSearch =
          doc.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
          doc.content.toLowerCase().includes(searchQuery.toLowerCase()) ||
          doc.summary.toLowerCase().includes(searchQuery.toLowerCase()) ||
          doc.tags.some(t => t.toLowerCase().includes(searchQuery.toLowerCase()));

      let matchesTabAndFolder = true;
      if (currentTab === "mydrive") {
        matchesTabAndFolder = selectedFolder === null ||
            isDescendantOrSelf(doc.folderId, selectedFolder, folders);
      } else if (currentTab === "starred") {
        matchesTabAndFolder = !!doc.star;
      } else if (currentTab === "secure") {
        matchesTabAndFolder = doc.securityRank === "기밀";
      } else if (currentTab === "recent") {
        matchesTabAndFolder = true;
      } else if (currentTab === "trash") {
        matchesTabAndFolder = true;
      }

      const matchesType = !selectedType || doc.fileType === selectedType;
      const matchesSecurity = !selectedSecurity || doc.securityRank === selectedSecurity;
      const matchesDocumentType = !selectedDocumentType || doc.documentType === selectedDocumentType;
      const matchesTag = !tagFilter.trim() || (doc.tags ?? []).some((t) => t.toLowerCase().includes(tagFilter.trim().toLowerCase()));
      const docDate = (doc.modifiedAt ?? "").slice(0, 10);
      const matchesDate = (!dateFromFilter || docDate >= dateFromFilter) && (!dateToFilter || docDate <= dateToFilter);

      return matchesSearch && matchesTabAndFolder && matchesType && matchesSecurity && matchesDocumentType && matchesTag && matchesDate;
    });
  }, [documents, serverSearchDocs, trashDocs, searchQuery, selectedFolder, selectedType, selectedSecurity, selectedDocumentType, tagFilter, dateFromFilter, dateToFilter, currentTab, folders]);

  const sortedFilteredDocuments = useMemo(() => {
    if (currentTab === "recent") {
      return [...filteredDocuments].sort((a, b) => b.modifiedAt.localeCompare(a.modifiedAt));
    }
    return filteredDocuments;
  }, [filteredDocuments, currentTab]);

  // Define FolderTreeNode interface & tree builder (folders 배열의 parentFolderId 관계로 직접 구성)
  interface FolderTreeNode {
    id: number;
    name: string;
    children: FolderTreeNode[];
    fileCount: number;
    totalFileCount: number;
  }

  const folderTree = useMemo(() => {
    const countTotal = (folderId: number): number => {
      const direct = documents.filter((d) => d.folderId === folderId).length;
      const childTotal = folders
        .filter((f) => f.parentFolderId === folderId)
        .reduce((sum, f) => sum + countTotal(f.folderId), 0);
      return direct + childTotal;
    };

    const buildNode = (folder: FolderType): FolderTreeNode => ({
      id: folder.folderId,
      name: folder.name,
      children: folders.filter((f) => f.parentFolderId === folder.folderId).map(buildNode),
      fileCount: documents.filter((d) => d.folderId === folder.folderId).length,
      totalFileCount: countTotal(folder.folderId)
    });

    return folders.filter((f) => f.parentFolderId === null).map(buildNode);
  }, [folders, documents]);

  // Find folders at the current directory level
  const currentLevelFolders = useMemo(() => {
    if (currentTab !== "mydrive") return [];
    return folders.filter((f) => f.parentFolderId === selectedFolder);
  }, [folders, selectedFolder, currentTab]);

  // Find files at the current directory level (only direct files during browsing, or all matching files during search/other tab views)
  const currentLevelDocuments = useMemo(() => {
    if (currentTab !== "mydrive") {
      // For other tabs (starred, secure, recent, trash), we display everything matching in a flat list
      return sortedFilteredDocuments;
    }
    
    // If there is an active search query, show all recursively matching documents under the current directory branch
    if (searchQuery) {
      return sortedFilteredDocuments;
    }
    
    // Otherwise, show only direct documents of the current selected folder level
    return sortedFilteredDocuments.filter(doc => doc.folderId === selectedFolder);
  }, [sortedFilteredDocuments, selectedFolder, currentTab, searchQuery]);

  // Determine if both folders and files at the current view level are empty
  const isWorkspaceEmpty = useMemo(() => {
    if (currentTab === "mydrive" && !searchQuery) {
      return currentLevelFolders.length === 0 && currentLevelDocuments.length === 0;
    }
    if (currentTab === "trash") {
      return currentLevelDocuments.length === 0 && (trashFolders?.length ?? 0) === 0;
    }
    return currentLevelDocuments.length === 0;
  }, [currentTab, searchQuery, currentLevelFolders, currentLevelDocuments, trashFolders]);

  // Handle Drag events
  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);

    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      const file = e.dataTransfer.files[0];
      setUploadName(file.name);

      // Read text content
      setUploadFile(file);
    }
  };

  const handleToggleStar = async (docId: string) => {
    if (starPendingRef.current.has(docId)) return;
    const doc = documents.find((d) => d.id === docId);
    if (!doc) return;
    const current = !!doc.star;
    const next = !current;
    starPendingRef.current.add(docId);
    onUpdateDocuments((prev) => prev.map((d) => (d.id === docId ? { ...d, star: next } : d)));
    try {
      await toggleStar(Number(docId), next);
    } catch (err) {
      console.warn("[files] PATCH /api/v1/files/{id}/star 실패 - 롤백:", err);
      onUpdateDocuments((prev) => prev.map((d) => (d.id === docId ? { ...d, star: current } : d)));
      alert("중요 문서 설정에 실패했습니다.");
    } finally {
      starPendingRef.current.delete(docId);
    }
  };

  const handleRenameDocument = async (docId: string, currentName: string) => {
    const name = window.prompt("새 파일 이름을 입력하세요.", currentName);
    if (!name || !name.trim() || name.trim() === currentName) return;
    try {
      await renameFile(Number(docId), name.trim());
      onUpdateDocuments(
          documents.map((d) => (d.id === docId ? { ...d, name: name.trim() } : d))
      );
    } catch (err) {
      console.warn("[files] PATCH /api/v1/files/{id}/name 실패:", err);
      alert("이름 변경에 실패했습니다.");
    }
  };

  const handleMoveDocuments = async (docIds: string[], targetFolder: number | null) => {
    const fileIds = docIds.map(Number).filter((id) => Number.isFinite(id));
    try {
      await moveFiles(fileIds, targetFolder);
    } catch (err) {
      console.warn("[files] PATCH /api/v1/files/batch/move 실패:", err);
      alert("문서 이동에 실패했습니다.");
      return;
    }
    onUpdateDocuments(
        documents.map((d) => (docIds.includes(d.id) ? { ...d, folderId: targetFolder } : d))
    );
    setCheckedDocIds([]);
    alert(`선택하신 ${docIds.length}개의 문서가 '${getFolderPath(targetFolder, folders) || "내 드라이브"}' 폴더로 성공적으로 이동되었습니다.`);
  };

  const handleDeleteDocuments = async (docIds: string[]) => {
    if (!window.confirm(`${docIds.length}개의 문서를 휴지통으로 이동하시겠습니까?`)) return;
    const ids = docIds.map(Number).filter((id) => Number.isFinite(id));
    const results = await Promise.allSettled(ids.map((id) => deleteFile(id)));
    const deletedIds = ids.filter((_, i) => results[i].status === "fulfilled").map(String);
    if (deletedIds.length < ids.length) {
      console.warn("[files] DELETE /api/v1/files/{id} 일부 실패(로그인 연동 전이면 정상):", ids.length - deletedIds.length);
    }
    onUpdateDocuments(documents.filter((d) => !deletedIds.includes(d.id)));
    setCheckedDocIds([]);
    alert(`${deletedIds.length}개의 문서를 휴지통으로 이동했습니다.`);
  };

  const handleRestoreDocuments = async (docIds: string[]) => {
    const ids = docIds.map(Number).filter((id) => Number.isFinite(id));
    const results = await Promise.allSettled(ids.map((id) => restoreFile(id)));
    const restoredIds = ids.filter((_, i) => results[i].status === "fulfilled").map(String);
    const failed = results.length - restoredIds.length;
    if (failed > 0) {
      console.warn("[files] PATCH /api/v1/files/{id}/restore 일부 실패(비로그인 상태면 정상):", failed);
    }
    if (restoredIds.length > 0) {
      setTrashDocs((prev) => (prev ? prev.filter((d) => !restoredIds.includes(d.id)) : prev));
      try {
        onUpdateDocuments(await listAllFiles());
      } catch (err) {
        console.warn("[files] 복원 후 목록 재동기화 실패:", err);
      }
      alert(`${restoredIds.length}개의 문서를 복원했습니다.`);
    } else {
      alert("문서 복원에 실패했습니다.");
    }
    setCheckedDocIds([]);
  };

  const handlePermanentDeleteDocuments = async (docIds: string[]) => {
    if (!window.confirm(`${docIds.length}개의 문서를 영구 삭제하시겠습니까? 되돌릴 수 없습니다.`)) return;
    const ids = docIds.map(Number).filter((id) => Number.isFinite(id));
    const results = await Promise.allSettled(ids.map((id) => permanentDeleteFile(id)));
    const deletedIds = ids.filter((_, i) => results[i].status === "fulfilled").map(String);
    if (deletedIds.length < ids.length) {
      console.warn("[files] 영구 삭제 일부 실패:", ids.length - deletedIds.length);
    }
    if (deletedIds.length > 0) {
      setTrashDocs((prev) => (prev ? prev.filter((d) => !deletedIds.includes(d.id)) : prev));
      getStorageUsage().then(setStorage).catch(() => {});
      alert(`${deletedIds.length}개의 문서를 영구 삭제했습니다.`);
    } else {
      alert("영구 삭제에 실패했습니다.");
    }
    setCheckedDocIds([]);
  };

  const mimeByType: Record<string, string> = {
    pdf: "application/pdf",
    docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    txt: "text/plain",
  };

  const addFilesToUploadQueue = (files: File[]) => enqueue(files, selectedFolder);

  const handleNewUploaderDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setNewUploaderDragActive(false);
    addFilesToUploadQueue(Array.from(e.dataTransfer.files));
  };

  const handleNewUploaderPick = (e: React.ChangeEvent<HTMLInputElement>) => {
    addFilesToUploadQueue(Array.from(e.target.files ?? []));
    e.target.value = "";
  };

  const runNewUploaderUpload = () => startAll();

  const handleFormSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!uploadFile && !uploadContent.trim()) {
      alert("문서 파일을 첨부하거나 내용을 입력해 주세요.");
      return;
    }

    const sourceExt = uploadFile
        ? (uploadFile.name.split(".").pop()?.toLowerCase() || "txt")
        : "txt";
    const baseName = uploadName.replace(new RegExp(`\\.${sourceExt}$`, "i"), "");
    const formattedName = `${baseName}.${sourceExt}`;
    const file = uploadFile
        ? new File([uploadFile], formattedName, { type: uploadFile.type || mimeByType[sourceExt] || "text/plain" })
        : new File([uploadContent], formattedName, { type: mimeByType[sourceExt] ?? "text/plain" });

    setIsUploading(true);
    try {
      const { uploadId, fileIds } = await uploadFiles([file], selectedFolder);

      let status: string = "PENDING";
      for (let i = 0; i < 20 && status !== "COMPLETED" && status !== "FAILED" && status !== "CANCELLED"; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        status = (await getUploadStatus(uploadId)).status;
      }

      onUpdateDocuments(await listAllFiles());

      if (isSpecialUploadMode && fileIds[0] != null) {
        try {
          const detail = await getFileDetail(fileIds[0]);
          setAutoResultData({
            fileName: detail.name,
            targetFolder: getFolderPath(detail.folderId, folders),
            targetFolderId: detail.folderId,
            summary: detail.summary,
            tags: detail.tags ?? [],
            security: detail.securityRank,
          });
          setIsUploadOpen(false);
          setShowAutoResultModal(true);
        } catch {
          setIsUploadOpen(false);
        }
      } else {
        setIsUploadOpen(false);
        if (status === "COMPLETED") {
          alert(`업로드 완료: ${formattedName}`);
        } else if (status === "FAILED") {
          alert(`업로드는 되었으나 문서 처리에 실패했습니다: ${formattedName}`);
        } else if (status === "CANCELLED") {
          alert(`업로드가 취소되었습니다: ${formattedName}`);
        } else {
          alert(`업로드됨 · 문서 처리가 진행 중입니다: ${formattedName}`);
        }
      }
    } catch (err) {
      console.warn("[uploads] POST /api/v1/uploads 실패:", err);
      alert("업로드 중 오류가 발생했습니다. (로그인 연동 전이면 401로 실패할 수 있습니다)");
    } finally {
      setUploadFile(null);
      setUploadName("");
      setUploadContent("");
      setIsUploading(false);
      setIsSpecialUploadMode(false);
    }
  };

  // Trigger folder reorganization sequence — POST /api/v1/organize/propose 호출.
  // 비로그인 상태면 401이 나는 게 정상 — 안내만 하고 종료한다. 폴더 목록처럼
  // mock으로 폴백할 수 없다(AI 제안 자체가 서버에서만 생성 가능하므로).
  const handleOrganizeFolders = async () => {
    setIsOrganizing(true);
    setOrganizeStep(1);

    // Progressive step animations
    const step1 = setTimeout(() => setOrganizeStep(2), 800);
    const step2 = setTimeout(() => setOrganizeStep(3), 1600);

    try {
      const proposal = await proposeOrganization();
      // apply 재시도(응답 유실 후 재요청 등) 시 서버가 같은 요청인지 판단할 수 있도록,
      // 이 제안을 승인 대상으로 받는 시점에 키를 한 번만 만들어 붙여둔다 — 재시도해도
      // handleApplyOrganization은 이 organizeResult를 그대로 재사용하므로 키가 바뀌지 않는다.
      setOrganizeResult({ ...proposal, idempotencyKey: crypto.randomUUID() });
    } catch (err) {
      console.error("스마트 정리 제안 생성 실패:", err);
      const message =
        err instanceof ApiError && err.status === 401
          ? "로그인 후 이용할 수 있는 기능입니다."
          : "스마트 정리 제안 생성 중 오류가 발생했습니다.";
      alert(message);
    } finally {
      setIsOrganizing(false);
      clearTimeout(step1);
      clearTimeout(step2);
    }
  };

  // POST /api/v1/organize/apply — 제안을 그대로 백엔드에 되돌려보내 실제 파일 이동/이름변경 반영.
  // 응답(성공 여부 + held)을 alert가 아니라 applyResult 상태에 담아, 모달이 "결과" 화면으로
  // 전환되도록 한다 — 예전엔 응답을 버리고 모달을 바로 닫은 뒤 알림창만 띄워서, 일부만
  // 보류돼도 사용자가 뭐가 어떻게 됐는지 모달에서 다시 확인할 방법이 없었다.
  const handleApplyOrganization = async () => {
    if (!organizeResult) return;
    setIsApplyingOrganize(true);
    try {
      const result = await applyOrganization(organizeResult);
      setApplyResult(result);
      setSelectedFolder(null);
      // 폴더 목록/파일 목록이 서버에서 실제로 바뀌었으니 둘 다 다시 조회 — 실패해도(로그인 전) 기존 상태 유지.
      // documents는 App.tsx 상태라 여기서 직접 못 고치고 onUpdateDocuments로 통째로 갱신해야
      // 일반 문서 화면/폴더별 개수/대시보드/채팅이 전부 최신 상태를 보게 된다.
      listFolders()
        .then(setFolders)
        .catch(() => {});
      listAllFiles()
        .then(onUpdateDocuments)
        .catch(() => {});
    } catch (err) {
      console.error("스마트 정리 적용 실패:", err);
      alert("정리를 적용하는 중 오류가 발생했습니다.");
    } finally {
      setIsApplyingOrganize(false);
    }
  };

  // 미리보기(적용 전)/결과(적용 후) 모달을 모두 닫는다.
  const closeOrganizeModal = () => {
    setOrganizeResult(null);
    setApplyResult(null);
  };

  // newFolders 중 실제로 생성되는(=하나 이상의 "적용되는" 매핑이 직접 또는 자식 폴더를 통해
  // 간접적으로 참조하는) tempId 집합을 계산한다. 백엔드 OrganizeService.resolveTempIdsToCreate와
  // 같은 규칙(부모 체인까지 포함)을 그대로 흉내낸 것 — confidence 미달로 보류될 매핑만 참조하는
  // 폴더는 서버에서도 안 만들어지므로, 미리보기에서 "신규"라고 표시하면 실제 동작과 어긋난다.
  const computeTempIdsToCreate = (appliedMappings: FileMapping[], newFolders: ProposedFolder[]): Set<string> => {
    const parentTempIdByTempId = new Map<string, string | null>();
    newFolders.forEach((f) => parentTempIdByTempId.set(f.tempId, f.parentTempId));

    const tempIdsToCreate = new Set<string>();
    for (const mapping of appliedMappings) {
      let tempId = mapping.targetTempId;
      while (tempId && !tempIdsToCreate.has(tempId)) {
        tempIdsToCreate.add(tempId);
        tempId = parentTempIdByTempId.get(tempId) ?? null;
      }
    }
    return tempIdsToCreate;
  };

  // 아직 적용 전이면 confidence/민감도로 "적용될 매핑"을 추정, 적용 후면 실제 held 응답으로
  // 정확히 계산한다 — 어느 쪽이든 결과는 { appliedMappings, tempIdsToCreate } 형태로 통일해서 쓴다.
  const getOrganizeApplyPreview = (result: OrganizeProposal, applied: OrganizeApplyResponse | null) => {
    let appliedMappings: FileMapping[];
    if (applied) {
      appliedMappings = result.mappings.filter((m) => !applied.held.some((h) => h.fileId === m.fileId));
    } else {
      // 백엔드(OrganizeService.isBelowThreshold)와 같은 규칙을 따른다: 이 제안 안에 confidence를
      // 가진 매핑이 하나도 없으면(완전 레거시 제안) 필터링 없이 전부 적용될 것으로 본다. 반면
      // 하나라도 confidence가 있으면(=apply 시 민감도 조회가 실제로 일어남), confidence가 빠진
      // 매핑도 서버에서는 보류되므로 여기서 "적용될 것"으로 잘못 미리 보여주면 안 된다.
      const anyConfidencePresent = result.mappings.some((m) => m.confidence != null);
      appliedMappings = result.mappings.filter(
        (m) => !anyConfidencePresent || (m.confidence != null && m.confidence >= sensitivity)
      );
    }
    return { appliedMappings, tempIdsToCreate: computeTempIdsToCreate(appliedMappings, result.newFolders) };
  };

  // 제안 안의 새 폴더(tempId 체인)를 실제 표시용 전체 경로 문자열로 풀어준다.
  // parentTempId를 따라 올라가다 parentFolderId(기존 폴더)를 만나면 거기서부터는
  // 기존 folders 배열 기준 getFolderPath로 이어붙인다.
  const resolveProposedFolderPath = (folder: ProposedFolder, newFolders: ProposedFolder[]): string => {
    const segments: string[] = [folder.name];
    let current: ProposedFolder | undefined = folder;
    while (current?.parentTempId) {
      const parent = newFolders.find((f) => f.tempId === current!.parentTempId);
      if (!parent) break;
      segments.unshift(parent.name);
      current = parent;
    }
    if (current?.parentFolderId != null) {
      const basePath = getFolderPath(current.parentFolderId, folders);
      return basePath ? `${basePath}/${segments.join("/")}` : segments.join("/");
    }
    return segments.join("/");
  };

  // 파일 매핑 하나의 "현재 위치/이름 → 목표 위치(/새 이름)"을 미리보기용으로 풀어준다.
  // documents에 없는 fileId(예: 목록을 아직 못 불러온 경우)는 방어적으로 "알 수 없음"으로 표시.
  const resolveMappingDisplay = (mapping: FileMapping, newFolders: ProposedFolder[]) => {
    const doc = documents.find((d) => Number(d.id) === mapping.fileId);
    const currentPath = doc ? (getFolderPath(doc.folderId, folders) || "미분류") : "알 수 없음";
    const currentName = doc?.name ?? `(fileId: ${mapping.fileId})`;

    let targetPath: string;
    if (mapping.targetTempId) {
      const targetFolder = newFolders.find((f) => f.tempId === mapping.targetTempId);
      targetPath = targetFolder ? resolveProposedFolderPath(targetFolder, newFolders) : "알 수 없음";
    } else if (mapping.targetFolderId != null) {
      targetPath = getFolderPath(mapping.targetFolderId, folders) || "미분류";
    } else {
      targetPath = "미분류";
    }

    return { currentName, currentPath, targetPath, newName: mapping.newName, confidence: mapping.confidence ?? null };
  };

  const handleSmartUploadTrigger = () => {
    setIsSpecialUploadMode(true);
    setIsUploadOpen(true);
  };

  const handleLoadSample = (key: string) => {
    if (key === "sample1") {
      setUploadName("2024년_인공지능_클라우드_바우처_결과보고서");
      setUploadContent(`[2024년 정보통신산업진흥원 클라우드 서비스 바우처 최종 보고서]
과제명: AI-Drive 지능형 협업 솔루션 개발 및 실증 사업.
총 예산: 480,000,000원 (정부지원금 3억 5천만 원, 민간부담금 1억 3천만 원)
실적 요약:
- 기업 문서 분류 정확도 94.2% 달성 (목표치 90% 대비 초과 달성)
- RAG 문서 추출 실시간 매칭 레이턴시 0.8초 구현 완료.
- 실시간 비밀유지 계약 및 PII 민감 개인 정보 식별 차단 필터 모듈 탑재 완료.
연구 책임자: 김민수 수석 (010-4433-2211, minsoo.kim@aidrive.ai)`);
    } else if (key === "sample2") {
      setUploadName("사내_복리후생_규정집_수정본");
      setUploadContent(`[사내 복리후생 가이드 - 인사운영팀 편찬]
인재의 건강한 균형 발전을 위한 주요 혜택:
1. 연간 선택적 복지 포인트 240만 원 지급 (매분기 60만 원 분할 부여)
2. 본인 및 부모, 자녀 종합 실손 의료보험 가입 지원 (연간 최대 500만 원 보상한도)
3. 주택자금 저금리 사내 대출 이자 보전 (최대 1억 원 원금 지원, 연 1.5% 보전금리)
주의 사항: 사내 내부 문서로써 협력 개발팀 외 제3자 무단 발설을 불허함.`);
    }
  };

  /**
   * 폴더 삭제(소프트) — 이제 서버가 하위 폴더·파일을 전부 휴지통으로 함께 보낸다.
   * 예전엔(하드 삭제 시절) 삭제 전에 안의 파일을 [미분류]로 미리 옮겨야 했지만, 지금 그렇게
   * 하면 그 파일들이 이 폴더의 소프트 삭제 대상(folderId 기준)에서 빠져버려서 휴지통이 아니라
   * 그냥 활성 상태로 [미분류]에 남는다 — 그래서 moveFiles 호출을 없애고 deleteFolder만 부른다.
   */
  const handleDeleteFolder = async (folderId: number, folderName: string) => {
    const collectDescendantIds = (id: number): number[] => {
      const childIds = folders.filter((f) => f.parentFolderId === id).map((f) => f.folderId);
      return [id, ...childIds.flatMap(collectDescendantIds)];
    };
    const idsToDelete = collectDescendantIds(folderId);
    const affectedDocs = documents.filter(
        (d) => d.folderId !== null && idsToDelete.includes(d.folderId)
    );
    const affectedDocCount = affectedDocs.length;

    const subFolderCount = idsToDelete.length - 1;
    const confirmMsg =
        `'${folderName}' 폴더를 삭제하시겠습니까?` +
        (subFolderCount > 0 ? `\n하위 폴더 ${subFolderCount}개도 함께 휴지통으로 이동됩니다.` : "") +
        (affectedDocCount > 0 ? `\n포함된 문서 ${affectedDocCount}개도 휴지통으로 이동됩니다.` : "");

    if (!window.confirm(confirmMsg)) return;

    try {
      await deleteFolder(folderId);
    } catch (err) {
      console.warn("[folders] 폴더 삭제 실패 - 상태를 변경하지 않음:", err);
      alert("폴더 삭제에 실패했습니다.");
      return;
    }

    setFolders((prev) => prev.filter((f) => !idsToDelete.includes(f.folderId)));

    if (affectedDocCount > 0) {
      const affectedDocIds = new Set(affectedDocs.map((d) => d.id));
      onUpdateDocuments((prev) => prev.filter((d) => !affectedDocIds.has(d.id)));
    }

    if (selectedFolder !== null && idsToDelete.includes(selectedFolder)) {
      setSelectedFolder(null);
    }

    // 왼쪽 사이드바 폴더 트리는 탭과 무관하게 항상 떠 있어서, "휴지통" 탭을 보고 있는 채로도
    // 여기서 폴더를 삭제할 수 있다. trashFolders/trashDocs는 탭이 "trash"로 바뀔 때만 다시
    // 불러오므로(currentTab이 이미 "trash"면 그 effect가 다시 안 돈다), 방금 삭제한 폴더와
    // 그 안의 파일이 휴지통 탭에 바로 안 보이는 문제가 있었다 — 그래서 여기서도 직접 반영한다.
    if (currentTab === "trash") {
      try {
        const [freshTrashFolders, freshTrashDocs] = await Promise.all([
          listAllFolderTrash(),
          listAllTrash(),
        ]);
        setTrashFolders(freshTrashFolders);
        setTrashDocs(freshTrashDocs);
      } catch (err) {
        console.warn("[folders] 삭제 후 휴지통 목록 재동기화 실패:", err);
      }
    }
  };

  /**
   * 휴지통 폴더 복원 — 서버는 이 폴더의 하위 폴더·파일까지 전부 함께 복원한다.
   * 방금 누른 폴더 하나만 trashFolders에서 지우면, 같이 복원된 하위 폴더·파일은 이미
   * 활성 상태가 됐는데도 휴지통 화면엔 그대로 남아서 다시 복원/영구삭제를 시도하면
   * "이미 처리된 리소스"라 실패한다 — 그래서 활성/휴지통 목록을 전부 다시 불러온다.
   */
  const handleRestoreFolder = async (folderId: number, folderName: string) => {
    try {
      await restoreFolder(folderId);
    } catch (err) {
      console.warn("[folders] PATCH /api/v1/folders/{id}/restore 실패:", err);
      alert("폴더 복원에 실패했습니다.");
      return;
    }
    try {
      const [freshFolders, freshDocs, freshTrashFolders, freshTrashDocs] = await Promise.all([
        listFolders(),
        listAllFiles(),
        listAllFolderTrash(),
        listAllTrash(),
      ]);
      setFolders(freshFolders);
      onUpdateDocuments(freshDocs);
      setTrashFolders(freshTrashFolders);
      setTrashDocs(freshTrashDocs);
    } catch (err) {
      console.warn("[folders] 복원 후 목록 재동기화 실패:", err);
    }
    alert(`'${folderName}' 폴더를 복원했습니다.`);
  };

  /**
   * 휴지통 폴더 영구 삭제 — 하위 파일 S3 실물까지 서버가 함께 정리한다.
   * 복원과 같은 이유로, 선택한 폴더 하나만 trashFolders에서 지우면 같이 영구삭제된
   * 하위 폴더·파일이 휴지통 화면에 유령처럼 남으므로 휴지통 목록을 통째로 재동기화한다.
   */
  const handlePermanentDeleteFolder = async (folderId: number, folderName: string) => {
    if (!window.confirm(`'${folderName}' 폴더를 영구 삭제하시겠습니까? 안의 파일까지 모두 되돌릴 수 없습니다.`)) return;
    try {
      await permanentDeleteFolder(folderId);
    } catch (err) {
      console.warn("[folders] DELETE /api/v1/folders/{id}/permanent 실패:", err);
      alert("폴더 영구 삭제에 실패했습니다.");
      return;
    }
    try {
      const [freshTrashFolders, freshTrashDocs] = await Promise.all([
        listAllFolderTrash(),
        listAllTrash(),
      ]);
      setTrashFolders(freshTrashFolders);
      setTrashDocs(freshTrashDocs);
    } catch (err) {
      console.warn("[folders] 영구 삭제 후 휴지통 재동기화 실패:", err);
    }
    getStorageUsage().then(setStorage).catch(() => {});
    alert(`'${folderName}' 폴더를 영구 삭제했습니다.`);
  };

  const handleRenameFolder = async (folderId: number, currentName: string) => {
    const input = window.prompt("폴더의 새 이름을 입력하세요.", currentName);
    if (input === null) return;
    const name = input.trim();
    if (!name || name === currentName) return;
    try {
      await updateFolder(folderId, { name });
      const refreshed = await listFolders();
      setFolders(refreshed);
    } catch (err) {
      console.warn("[folders] 폴더 이름 변경 실패 - 상태를 변경하지 않음:", err);
      alert("폴더 이름 변경에 실패했습니다.");
    }
  };

  const handleMoveFolder = async (folderId: number | null, targetParentId: number | null) => {
    if (folderId === null) return;
    try {
      await updateFolder(folderId, { parentFolderId: targetParentId });
      const refreshed = await listFolders();
      setFolders(refreshed);
    } catch (err) {
      console.warn("[folders] 폴더 이동 실패 - 상태를 변경하지 않음:", err);
      alert("폴더 이동에 실패했습니다.");
    }
  };

  const renderFolderNode = (node: FolderTreeNode, depth: number = 0): React.ReactNode => {
    const hasChildren = node.children.length > 0;
    const isExpanded = expandedFolders[node.id] ?? true; // Default expanded
    const isSelected = currentTab === "mydrive" && selectedFolder === node.id;

    const toggleExpand = (e: React.MouseEvent) => {
      e.stopPropagation();
      setExpandedFolders(prev => ({ ...prev, [node.id]: !isExpanded }));
    };

    return (
      <div key={node.id} className="space-y-1">
        <div 
          onClick={() => {
            setCurrentTab("mydrive");
            setSelectedFolder(node.id);
          }}
          className={`group py-2 px-2.5 rounded-r-full flex items-center justify-between cursor-pointer gap-2 transition-all mr-1.5 ${
            isSelected 
              ? "bg-primary/10 text-primary font-bold border-l-4 border-primary pl-1.5" 
              : "text-outline hover:text-on-surface hover:bg-surface-container-low pl-2"
          }`}
        >
          <div className="flex items-center gap-1.5 min-w-0 flex-1">
            {hasChildren ? (
              <button 
                onClick={toggleExpand}
                className="p-0.5 hover:bg-black/5 dark:hover:bg-white/5 rounded transition-colors shrink-0 cursor-pointer flex items-center justify-center"
              >
                <ChevronRight className={`w-3.5 h-3.5 transition-transform duration-200 ${isExpanded ? "rotate-90" : ""} ${isSelected ? "text-primary" : "text-outline"}`} />
              </button>
            ) : (
              <span className="w-4.5 h-4.5 shrink-0" />
            )}

            {isSelected ? (
              <Folder className="w-4 h-4 text-primary fill-primary/10 shrink-0" />
            ) : (
              <FolderClosed className="w-4 h-4 text-outline shrink-0 group-hover:text-primary transition-colors" />
            )}

            <span className="text-xs font-semibold truncate" title={node.name}>{node.name}</span>
          </div>

          <div className="flex items-center gap-1 shrink-0">
            <span className={`text-[9px] px-1.5 py-0.5 rounded font-bold ${
              isSelected ? "bg-primary/20 text-primary" : "bg-surface-container-low text-outline"
            }`}>
              {node.totalFileCount}
            </span>
            <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  handleRenameFolder(node.id, node.name);
                }}
                className="p-1 rounded opacity-0 group-hover:opacity-100 text-outline hover:text-primary hover:bg-primary/5 transition-all cursor-pointer"
                title="폴더 이름 변경"
            >
              <Pencil className="w-3 h-3" />
            </button>
            <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  setFolderMoveTarget(null);
                  setMovingFolderId(node.id);
                }}
                className="p-1 rounded opacity-0 group-hover:opacity-100 text-outline hover:text-primary hover:bg-primary/5 transition-all cursor-pointer"
                title="폴더 이동"
            >
              <FolderInput className="w-3 h-3" />
            </button>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                handleDeleteFolder(node.id, node.name);
              }}
              className="p-1 rounded opacity-0 group-hover:opacity-100 text-outline hover:text-rose-500 hover:bg-rose-50 transition-all cursor-pointer"
              title="폴더 삭제"
            >
              <Trash2 className="w-3 h-3" />
            </button>
          </div>
        </div>

        {hasChildren && isExpanded && (
          <div className="space-y-1 border-l border-outline-variant/30 ml-4 pl-2">
            {node.children.map(child => renderFolderNode(child, depth + 1))}
          </div>
        )}
      </div>
    );
  };

  return (
    <motion.div 
      initial={{ opacity: 0, y: 15 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -15 }}
      className="space-y-8"
      id="my-documents-view"
    >
      {/* Breadcrumb, Title & AI Smart Organize Action */}
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-6" id="documents-header-container">
        <div>
          <nav className="flex text-outline font-medium text-xs mb-2 gap-2 items-center" id="doc-breadcrumbs">
            <span className="hover:text-primary cursor-pointer" onClick={() => setSelectedFolder(null)}>Drive</span>
            <ChevronRight className="w-3.5 h-3.5 text-outline-variant" />
            <span className="text-primary font-bold">내 문서</span>
          </nav>
          <h2 className="text-3xl font-bold tracking-tight text-on-surface font-sans" id="doc-vault-title">문서 보관함</h2>
        </div>

        {/* AI 스마트 정리 & Actions Toolbar */}
        <div className="flex items-center gap-4 flex-wrap" id="doc-toolbar-actions">
          <button
              type="button"
              onClick={() => setIsNewUploadOpen(true)}
              className="flex items-center gap-2 bg-white border-2 border-primary/40 text-primary font-extrabold py-3 px-5 rounded-xl shadow-sm hover:bg-primary/5 hover:scale-[1.01] transition-all cursor-pointer text-body-sm active:scale-95"
              id="btn-new-multi-uploader"
          >
            <Upload className="w-4 h-4" />
            새 업로더 (다중·베타)
          </button>
          {/* AI 스마트 정리 Dropdown Trigger Button */}
          <div className="relative">
            <button 
              onClick={() => setIsSmartMenuOpen(!isSmartMenuOpen)}
              className="flex items-center gap-2 bg-gradient-to-r from-secondary to-primary text-white font-extrabold py-3 px-5 rounded-xl shadow-lg shadow-primary/10 hover:scale-[1.01] hover:shadow-xl transition-all cursor-pointer text-body-sm active:scale-95 border-b-2 border-primary/30"
              id="btn-ai-smart-organize"
            >
              <Sparkles className="w-4 h-4 text-secondary-container fill-secondary-container/20 animate-pulse" />
              AI 스마트 정리
              <ChevronDown className={`w-4.5 h-4.5 transition-transform duration-250 ${isSmartMenuOpen ? "rotate-180" : ""}`} />
            </button>

            <AnimatePresence>
              {isSmartMenuOpen && (
                <>
                  <div className="fixed inset-0 z-40" onClick={() => setIsSmartMenuOpen(false)}></div>
                  <motion.div 
                    initial={{ opacity: 0, y: 10, scale: 0.95 }}
                    animate={{ opacity: 1, y: 0, scale: 1 }}
                    exit={{ opacity: 0, y: 10, scale: 0.95 }}
                    transition={{ duration: 0.15 }}
                    className="absolute right-0 mt-3 w-80 bg-white rounded-2xl border border-outline-variant shadow-2xl z-50 overflow-hidden"
                    id="smart-organize-dropdown"
                  >
                    <div className="p-4 bg-surface-container-lowest border-b border-outline-variant/40 flex items-center gap-2">
                      <Sparkles className="w-4.5 h-4.5 text-secondary fill-secondary/10" />
                      <h5 className="font-extrabold text-xs text-primary uppercase tracking-wider">지능형 자동 정리 도구</h5>
                    </div>

                    <div className="p-2 space-y-1">
                      <button
                        onClick={() => {
                          setIsSmartMenuOpen(false);
                          handleOrganizeFolders();
                        }}
                        className="w-full text-left p-3.5 rounded-xl hover:bg-primary/5 transition-colors flex gap-3.5 cursor-pointer group"
                      >
                        <div className="w-9 h-9 rounded-lg bg-secondary/10 text-secondary flex items-center justify-center shrink-0 group-hover:bg-secondary group-hover:text-white transition-colors">
                          <Folder className="w-5 h-5" />
                        </div>
                        <div className="min-w-0 flex-1">
                          <p className="font-bold text-xs text-on-surface group-hover:text-primary transition-colors">1) 현재 폴더 정리하기</p>
                          <p className="text-[10px] text-outline mt-0.5 leading-relaxed">
                            AI가 전체 문서를 분석해 새 폴더 구조와 파일 이동을 제안합니다.
                          </p>
                        </div>
                      </button>

                      <button 
                        onClick={() => {
                          setIsSmartMenuOpen(false);
                          handleSmartUploadTrigger();
                        }}
                        className="w-full text-left p-3.5 rounded-xl hover:bg-primary/5 transition-colors flex gap-3.5 cursor-pointer group"
                      >
                        <div className="w-9 h-9 rounded-lg bg-primary/10 text-primary flex items-center justify-center shrink-0 group-hover:bg-primary group-hover:text-white transition-colors">
                          <Upload className="w-5 h-5" />
                        </div>
                        <div className="min-w-0 flex-1">
                          <p className="font-bold text-xs text-on-surface group-hover:text-primary transition-colors">2) 업로드한 파일 자동 정리하기</p>
                          <p className="text-[10px] text-outline mt-0.5 leading-relaxed">새 서류를 올리면 AI가 문서를 완전 해독하여 적합한 폴더에 자동 배치합니다.</p>
                        </div>
                      </button>
                    </div>
                  </motion.div>
                </>
              )}
            </AnimatePresence>
          </div>

          {/* Quick Upload Action */}
          <button 
            onClick={() => {
              setIsSpecialUploadMode(false);
              setIsUploadOpen(true);
            }}
            className="py-3 px-5 bg-surface-container text-primary rounded-xl font-bold text-body-sm flex items-center gap-2 hover:bg-surface-container-high transition-colors cursor-pointer border border-outline-variant/30"
          >
            <Plus className="w-4 h-4 stroke-[2.5]" /> 문서 직접 추가
          </button>
        </div>
      </div>

      {/* Grid Layout of Tree Sidebar + Main Vault content */}
      <div className="grid grid-cols-12 gap-8" id="vault-layout-grid">
        
        {/* Left Side: Google Drive Navigation Folder Tree */}
        <aside className="col-span-12 lg:col-span-3 flex flex-col gap-5" id="gdrive-tree-sidebar">
          <div className="bg-white rounded-3xl border border-outline-variant p-5 shadow-sm space-y-4">
            
            {/* Google Drive styled + New Button */}
            <div className="pb-2 border-b border-outline-variant/20">
              <button
                onClick={() => {
                  setIsNewFolderModalOpen(true);
                }}
                className="w-full py-3 px-5 bg-white border border-outline-variant/50 rounded-full font-bold text-[13px] text-on-surface shadow-md hover:shadow-lg hover:bg-surface-container-low transition-all cursor-pointer flex items-center justify-center gap-3 active:scale-95 group"
              >
                <Plus className="w-5 h-5 text-primary stroke-[3] group-hover:rotate-90 transition-transform duration-200" />
                <span>새 폴더 만들기</span>
              </button>
            </div>

            {/* Sidebar Navigation Options */}
            <div className="space-y-1" id="gdrive-nav-list">
              
              {/* My Drive (내 드라이브) Root */}
              <div className="space-y-1">
                <div 
                  onClick={() => {
                    setCurrentTab("mydrive");
                    setSelectedFolder(null);
                  }}
                  className={`group py-2.5 px-3 rounded-r-full flex items-center justify-between cursor-pointer gap-2 transition-all mr-1.5 ${
                    currentTab === "mydrive" && selectedFolder === null
                      ? "bg-primary/10 text-primary font-bold border-l-4 border-primary pl-2" 
                      : "text-on-surface hover:bg-surface-container-low pl-3"
                  }`}
                  id="nav-my-drive-root"
                >
                  <div className="flex items-center gap-2 min-w-0 flex-1">
                    <button 
                      onClick={(e) => {
                        e.stopPropagation();
                        setIsMyDriveExpanded(!isMyDriveExpanded);
                      }}
                      className="p-0.5 hover:bg-black/5 dark:hover:bg-white/5 rounded transition-colors shrink-0 cursor-pointer flex items-center justify-center"
                    >
                      <ChevronRight className={`w-4 h-4 transition-transform duration-200 ${isMyDriveExpanded ? "rotate-90" : ""} ${currentTab === "mydrive" && selectedFolder === null ? "text-primary" : "text-outline"}`} />
                    </button>
                    <HardDrive className={`w-4 h-4 shrink-0 ${currentTab === "mydrive" && selectedFolder === null ? "text-primary" : "text-outline group-hover:text-primary transition-colors"}`} />
                    <span className="text-[13px] font-semibold truncate">내 드라이브</span>
                  </div>
                  <span className={`text-[9px] px-1.5 py-0.5 rounded font-bold shrink-0 ${
                    currentTab === "mydrive" && selectedFolder === null ? "bg-primary/20 text-primary" : "bg-surface-container-low text-outline"
                  }`}>
                    {documents.length}
                  </span>
                </div>

                {/* Indented folders under My Drive */}
                {isMyDriveExpanded && (
                  <div className="space-y-1 border-l border-outline-variant/30 ml-[23px] pl-3.5 max-h-[280px] overflow-y-auto custom-scrollbar">
                    {folderTree.length === 0 ? (
                      <p className="text-[10px] text-outline text-center py-2 italic">폴더가 비어 있습니다.</p>
                    ) : (
                      folderTree.map((node) => renderFolderNode(node, 0))
                    )}
                  </div>
                )}
              </div>

              {/* Starred (중요 문서함) */}
              <div 
                onClick={() => {
                  setCurrentTab("starred");
                  setSelectedFolder(null);
                }}
                className={`group py-2.5 px-3 rounded-r-full flex items-center justify-between cursor-pointer gap-2 transition-all mr-1.5 ${
                  currentTab === "starred"
                    ? "bg-primary/10 text-primary font-bold border-l-4 border-primary pl-2" 
                    : "text-on-surface hover:bg-surface-container-low pl-3"
                }`}
                id="nav-starred"
              >
                <div className="flex items-center gap-2 min-w-0 flex-1">
                  <span className="w-5 h-5 shrink-0" /> {/* Spacer to match the chevron space */}
                  <Star className={`w-4 h-4 shrink-0 ${currentTab === "starred" ? "text-amber-500 fill-amber-400" : "text-outline group-hover:text-amber-500 transition-colors"}`} />
                  <span className="text-[13px] font-semibold truncate">중요 문서함</span>
                </div>
                <span className={`text-[9px] px-1.5 py-0.5 rounded font-bold shrink-0 ${
                  currentTab === "starred" ? "bg-primary/20 text-primary" : "bg-surface-container-low text-outline"
                }`}>
                  {documents.filter((d) => d.star).length}
                </span>
              </div>

              {/* Security Vault (보안 격리소) */}
              <div 
                onClick={() => {
                  setCurrentTab("secure");
                  setSelectedFolder(null);
                }}
                className={`group py-2.5 px-3 rounded-r-full flex items-center justify-between cursor-pointer gap-2 transition-all mr-1.5 ${
                  currentTab === "secure"
                    ? "bg-primary/10 text-primary font-bold border-l-4 border-primary pl-2" 
                    : "text-on-surface hover:bg-surface-container-low pl-3"
                }`}
                id="nav-secure"
              >
                <div className="flex items-center gap-2 min-w-0 flex-1">
                  <span className="w-5 h-5 shrink-0" />
                  <ShieldAlert className={`w-4 h-4 shrink-0 ${currentTab === "secure" ? "text-rose-600" : "text-outline group-hover:text-rose-600 transition-colors"}`} />
                  <span className="text-[13px] font-semibold truncate">보안 격리소</span>
                </div>
                <span className={`text-[9px] px-1.5 py-0.5 rounded font-bold shrink-0 ${
                  currentTab === "secure" ? "bg-primary/20 text-primary" : "bg-surface-container-low text-outline"
                }`}>
                  {documents.filter(d => d.securityRank === "기밀").length}
                </span>
              </div>

              {/* Recent (최근 문서함) */}
              <div 
                onClick={() => {
                  setCurrentTab("recent");
                  setSelectedFolder(null);
                }}
                className={`group py-2.5 px-3 rounded-r-full flex items-center justify-between cursor-pointer gap-2 transition-all mr-1.5 ${
                  currentTab === "recent"
                    ? "bg-primary/10 text-primary font-bold border-l-4 border-primary pl-2" 
                    : "text-on-surface hover:bg-surface-container-low pl-3"
                }`}
                id="nav-recent"
              >
                <div className="flex items-center gap-2 min-w-0 flex-1">
                  <span className="w-5 h-5 shrink-0" />
                  <Clock className={`w-4 h-4 shrink-0 ${currentTab === "recent" ? "text-primary" : "text-outline group-hover:text-primary transition-colors"}`} />
                  <span className="text-[13px] font-semibold truncate">최근 문서함</span>
                </div>
              </div>

              {/* Trash (휴지통) */}
              <div 
                onClick={() => {
                  setCurrentTab("trash");
                  setSelectedFolder(null);
                }}
                className={`group py-2.5 px-3 rounded-r-full flex items-center justify-between cursor-pointer gap-2 transition-all mr-1.5 ${
                  currentTab === "trash"
                    ? "bg-primary/10 text-primary font-bold border-l-4 border-primary pl-2" 
                    : "text-on-surface hover:bg-surface-container-low pl-3"
                }`}
                id="nav-trash"
              >
                <div className="flex items-center gap-2 min-w-0 flex-1">
                  <span className="w-5 h-5 shrink-0" />
                  <Trash2 className={`w-4 h-4 shrink-0 ${currentTab === "trash" ? "text-red-500" : "text-outline group-hover:text-red-500 transition-colors"}`} />
                  <span className="text-[13px] font-semibold truncate">휴지통</span>
                </div>
              </div>

            </div>

            <div className="pt-4 border-t border-outline-variant/30 space-y-2 text-center" id="tree-sidebar-storage">
              <div className="flex justify-between items-center text-[10px] font-bold text-outline">
                <span>클라우드 총 용량</span>
                <span>{storage ? `${formatBytes(storage.usedBytes)} / ${formatBytes(storage.quotaBytes)}` : "-"}</span>
              </div>
              <div className="h-1.5 w-full bg-surface-container rounded-full overflow-hidden">
                <div className="h-full bg-primary rounded-full" style={{ width: `${storagePercent}%` }}></div>
              </div>
            </div>
          </div>
        </aside>

        {/* Right Side: Folder content details with Grid & List */}
        <section className="col-span-12 lg:col-span-9 space-y-6" id="vault-workspace">
          
          {/* Main Workspace topbar (Search & Toolbar) */}
          <div className="bg-white p-5 rounded-3xl border border-outline-variant shadow-sm space-y-4" id="vault-workspace-topbar">
            
            {/* Filter chips path display */}
            <div className="flex items-center justify-between flex-wrap gap-4 border-b border-outline-variant/30 pb-4">
              <div className="flex items-center gap-1.5 text-xs font-extrabold text-outline" id="active-path-indicator">
                <button
                  type="button"
                  onClick={() => {
                    setCurrentTab("mydrive");
                    setSelectedFolder(null);
                  }}
                  className="hover:text-primary transition-colors cursor-pointer flex items-center gap-1"
                >
                  <HardDrive className="w-3.5 h-3.5 text-outline-variant" />
                  Drive
                </button>
                
                {currentTab === "mydrive" && selectedFolder !== null ? (
                  <>
                    {getFolderAncestors(selectedFolder, folders).map((ancestor, index, arr) => (
                        <div key={ancestor.folderId} className="flex items-center gap-1.5">
                          <ChevronRight className="w-3 h-3 text-outline-variant shrink-0" />
                          <button
                            type="button"
                            onClick={() => setSelectedFolder(ancestor.folderId)}
                            className={`hover:text-primary hover:underline transition-colors cursor-pointer max-w-[120px] truncate ${
                              index === arr.length - 1 ? "text-primary bg-primary/5 px-2.5 py-1 rounded-full border border-primary/10" : ""
                            }`}
                          >
                            {ancestor.name}
                          </button>
                        </div>
                      ))}
                  </>
                ) : currentTab !== "mydrive" ? (
                  <>
                    <ChevronRight className="w-3 h-3 text-outline-variant shrink-0" />
                    <span className="text-primary bg-primary/5 px-2.5 py-1 rounded-full border border-primary/10">
                      {currentTab === "starred" ? "중요 문서함"
                        : currentTab === "secure" ? "보안 격리소"
                        : currentTab === "recent" ? "최근 문서함"
                        : "휴지통"}
                    </span>
                  </>
                ) : (
                  <>
                    <ChevronRight className="w-3 h-3 text-outline-variant shrink-0" />
                    <span className="text-primary bg-primary/5 px-2.5 py-1 rounded-full border border-primary/10">
                      내 드라이브 전체
                    </span>
                  </>
                )}
              </div>

              {/* Layout view controls */}
              <div className="flex bg-surface-container-low p-1 rounded-xl border border-outline-variant" id="view-mode-toggles">
                <button 
                  onClick={() => setViewMode("grid")}
                  className={`px-3 py-1.5 rounded-lg text-xs flex items-center gap-1.5 cursor-pointer transition-all font-bold ${
                    viewMode === "grid" ? "bg-white shadow-sm text-primary" : "text-outline hover:text-on-surface"
                  }`}
                >
                  <Grid className="w-3.5 h-3.5" /> 격자형
                </button>
                <button 
                  onClick={() => setViewMode("list")}
                  className={`px-3 py-1.5 rounded-lg text-xs flex items-center gap-1.5 cursor-pointer transition-all font-bold ${
                    viewMode === "list" ? "bg-white shadow-sm text-primary" : "text-outline hover:text-on-surface"
                  }`}
                >
                  <List className="w-3.5 h-3.5" /> 리스트형
                </button>
              </div>
            </div>

            {/* Quick dropdown filters */}
            <div className="flex items-center gap-2.5 flex-wrap" id="workspace-quick-filters">
              {/* Type Filter */}
              <select 
                value={selectedType || ""} 
                onChange={(e) => setSelectedType(e.target.value || null)}
                className="bg-white border border-outline-variant rounded-xl py-2 px-3 text-xs font-semibold text-on-surface-variant focus:ring-2 focus:ring-primary/10 outline-none cursor-pointer hover:border-outline transition-colors"
              >
                <option value="">파일 유형 전체</option>
                <option value="pdf">PDF 문서</option>
                <option value="docx">WORD 서류</option>
                <option value="xlsx">EXCEL 스프레드시트</option>
                <option value="txt">TEXT 텍스트</option>
              </select>

              {/* Security Level Filter */}
              <select 
                value={selectedSecurity || ""} 
                onChange={(e) => setSelectedSecurity(e.target.value || null)}
                className="bg-white border border-outline-variant rounded-xl py-2 px-3 text-xs font-semibold text-on-surface-variant focus:ring-2 focus:ring-primary/10 outline-none cursor-pointer hover:border-outline transition-colors"
              >
                <option value="">보안 조치 등급 전체</option>
                <option value="일반">일반 등급</option>
                <option value="기밀">기밀 등급 (PII 감지)</option>
              </select>

              <select
                  value={selectedDocumentType || ""}
                  onChange={(e) => setSelectedDocumentType(e.target.value || null)}
                  className="bg-white border border-outline-variant rounded-xl py-2 px-3 text-xs font-semibold text-on-surface-variant focus:ring-2 focus:ring-primary/10 outline-none cursor-pointer hover:border-outline transition-colors"
              >
                <option value="">문서 종류 전체</option>
                {documentTypeOptions.map((opt) => (
                    <option key={opt} value={opt}>{opt}</option>
                ))}
              </select>

              <input
                  type="text"
                  value={tagFilter}
                  onChange={(e) => setTagFilter(e.target.value)}
                  placeholder="태그 검색"
                  className="bg-white border border-outline-variant rounded-xl py-2 px-3 text-xs font-semibold text-on-surface-variant focus:ring-2 focus:ring-primary/10 outline-none hover:border-outline transition-colors w-28"
              />

              <input
                  type="date"
                  value={dateFromFilter}
                  onChange={(e) => setDateFromFilter(e.target.value)}
                  className="bg-white border border-outline-variant rounded-xl py-2 px-3 text-xs font-semibold text-on-surface-variant focus:ring-2 focus:ring-primary/10 outline-none cursor-pointer hover:border-outline transition-colors"
              />
              <span className="text-xs text-outline">~</span>
              <input
                  type="date"
                  value={dateToFilter}
                  onChange={(e) => setDateToFilter(e.target.value)}
                  className="bg-white border border-outline-variant rounded-xl py-2 px-3 text-xs font-semibold text-on-surface-variant focus:ring-2 focus:ring-primary/10 outline-none cursor-pointer hover:border-outline transition-colors"
              />

              {/* Filter Reset */}
              {(selectedFolder || selectedType || selectedSecurity || searchQuery || selectedDocumentType || tagFilter || dateFromFilter || dateToFilter) && (
                <button 
                  onClick={() => {
                    setSelectedFolder(null);
                    setSelectedType(null);
                    setSelectedSecurity(null);
                    setSearchQuery("");
                    setSelectedDocumentType(null);
                    setTagFilter("");
                    setDateFromFilter("");
                    setDateToFilter("");
                  }}
                  className="px-3 py-2 bg-rose-50 hover:bg-rose-100 text-rose-600 rounded-xl transition-all cursor-pointer font-bold text-xs flex items-center gap-1 border border-rose-100"
                >
                  필터 초기화
                </button>
              )}
            </div>

            {/* Inline search box inside workspace */}
            <div className="relative w-full group" id="workspace-search-box">
              <Search className="w-4.5 h-4.5 absolute left-4 top-1/2 -translate-y-1/2 text-outline group-focus-within:text-primary transition-colors" />
              <input 
                type="text" 
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="현재 열린 폴더 및 드라이브 내 문서 검색..." 
                className="w-full bg-white border border-outline-variant rounded-2xl py-3 pl-11 pr-4 focus:outline-none focus:ring-2 focus:ring-primary/10 focus:border-primary text-body-sm font-semibold transition-all shadow-sm"
              />
              {searchQuery && (
                <button onClick={() => setSearchQuery("")} className="absolute right-4 top-1/2 -translate-y-1/2 text-outline hover:text-on-surface">
                  <X className="w-4 h-4" />
                </button>
              )}
            </div>
          </div>

          {/* Documents render stack (Grid vs List) */}
          <div className="bg-white rounded-3xl border border-outline-variant overflow-hidden shadow-sm" id="vault-documents-list-section">
            <div className="px-8 py-5 border-b border-outline-variant flex justify-between items-center bg-surface-container-lowest" id="vault-docs-header">
              <div className="flex items-center gap-2">
                <h3 className="text-base font-extrabold text-on-surface">
                  {currentTab === "mydrive" 
                    ? (selectedFolder !== null ? `[${getFolderPath(selectedFolder, folders)}] 내부 파일` : "내 드라이브 전체 문서")
                    : currentTab === "starred" ? "중요 문서함 (Starred)"
                    : currentTab === "secure" ? "보안 격리소 내부 기밀문서"
                    : currentTab === "recent" ? "최근 수정된 문서"
                    : "휴지통"
                  }
                </h3>
                <span className="text-[10px] bg-primary/5 text-primary px-2.5 py-1 rounded-full font-extrabold">
                  총 {filteredDocuments.length}개
                </span>
              </div>
              {serverSearchDocs && (
                  <div className="flex items-center gap-3" id="vault-server-search-pagination">
                  <span className="text-[10px] text-outline font-bold">
                    서버 검색 {searchTotal}건 · {searchPage + 1} / {Math.max(1, Math.ceil(searchTotal / searchSize))} 페이지
                  </span>
                    <button
                        onClick={() => setSearchPage((p) => Math.max(0, p - 1))}
                        disabled={searchPage === 0}
                        className="text-[11px] px-2.5 py-1 rounded-full border border-outline-variant font-bold disabled:opacity-40 hover:bg-surface-container cursor-pointer disabled:cursor-not-allowed"
                    >
                      이전
                    </button>
                    <button
                        onClick={() => setSearchPage((p) => p + 1)}
                        disabled={(searchPage + 1) * searchSize >= searchTotal}
                        className="text-[11px] px-2.5 py-1 rounded-full border border-outline-variant font-bold disabled:opacity-40 hover:bg-surface-container cursor-pointer disabled:cursor-not-allowed"
                    >
                      다음
                    </button>
                  </div>
              )}
            </div>

            {/* Batch Action Bar */}
            {checkedDocIds.length > 0 && (
              <div className="m-6 bg-primary/5 border border-primary/10 p-4 rounded-2xl flex items-center justify-between gap-4 animate-in fade-in slide-in-from-top-2">
                <div className="flex items-center gap-3">
                  <span className="text-xs font-extrabold text-primary">{checkedDocIds.length}개 문서가 선택되었습니다.</span>
                  <button 
                    onClick={() => setCheckedDocIds([])}
                    className="text-[11px] text-outline hover:text-on-surface font-bold underline cursor-pointer"
                  >
                    선택 해제
                  </button>
                </div>
                <div className="flex items-center gap-2">
                  {currentTab === "trash" && (
                      <>
                        <button
                            onClick={() => handleRestoreDocuments(checkedDocIds)}
                            className="px-3.5 py-2 bg-primary text-white text-[11px] font-extrabold rounded-xl hover:bg-opacity-95 transition-all flex items-center gap-1.5 cursor-pointer shadow-sm"
                        >
                          <Undo2 className="w-3.5 h-3.5" /> 선택 복원
                        </button>
                        <button
                            onClick={() => handlePermanentDeleteDocuments(checkedDocIds)}
                            className="px-3.5 py-2 bg-red-600 text-white text-[11px] font-extrabold rounded-xl hover:bg-red-700 transition-all flex items-center gap-1.5 cursor-pointer shadow-sm"
                        >
                          <Trash2 className="w-3.5 h-3.5" /> 선택 영구 삭제
                        </button>
                      </>
                  )}
                  {currentTab !== "trash" && (
                      <>
                        <button
                            onClick={() => {
                              setMovingDocIds(checkedDocIds);
                              const firstDoc = documents.find(d => d.id === checkedDocIds[0]);
                              setMoveTargetFolder(firstDoc ? firstDoc.folderId : null);
                              setIsMoveModalOpen(true);
                            }}
                            className="px-3.5 py-2 bg-primary text-white text-[11px] font-extrabold rounded-xl hover:bg-opacity-95 transition-all flex items-center gap-1.5 cursor-pointer shadow-sm"
                        >
                          <FolderInput className="w-3.5 h-3.5" /> 폴더로 일괄 이동
                        </button>
                        <button
                            onClick={() => handleDeleteDocuments(checkedDocIds)}
                            className="px-3.5 py-2 bg-red-500 text-white text-[11px] font-extrabold rounded-xl hover:bg-opacity-95 transition-all flex items-center gap-1.5 cursor-pointer shadow-sm"
                        >
                          <Trash2 className="w-3.5 h-3.5" /> 휴지통으로 이동
                        </button>
                        <button
                            onClick={() => onNavigateToChat(checkedDocIds)}
                            className="px-3.5 py-2 bg-secondary text-white text-[11px] font-extrabold rounded-xl hover:bg-opacity-95 transition-all flex items-center gap-1.5 cursor-pointer shadow-sm"
                        >
                          <Sparkles className="w-3.5 h-3.5 animate-pulse" /> AI RAG 일괄 질문
                        </button>
                      </>
                  )}
                </div>
              </div>
            )}

            {isWorkspaceEmpty ? (
              <div className="p-16 text-center" id="empty-state-vault">
                <FolderClosed className="w-16 h-16 text-outline-variant mx-auto mb-4 stroke-[1.5]" />
                <p className="text-body-md font-extrabold text-on-surface">일치하는 문서 파일이나 폴더가 없습니다.</p>
                <p className="text-xs text-outline mt-1 leading-relaxed">검색어 필터를 리셋하시거나 'AI 스마트 정리' 버튼을 이용해 분류해 보세요.</p>
                <button 
                  onClick={() => {
                    setSelectedFolder(null);
                    setSelectedType(null);
                    setSelectedSecurity(null);
                    setSearchQuery("");
                  }}
                  className="mt-4 px-4 py-2 text-primary font-bold border border-primary rounded-xl hover:bg-primary/5 transition-colors cursor-pointer text-xs"
                >
                  모든 문서 리로드
                </button>
              </div>
            ) : viewMode === "grid" ? (
              /* Grid Layout view */
              <div className="p-8 space-y-8" id="vault-grid-list">
                {/* 1. Folders Section (Only shown in MyDrive browsing mode) */}
                {currentTab === "mydrive" && !searchQuery && currentLevelFolders.length > 0 && (
                  <div className="space-y-3.5">
                    <h4 className="text-xs font-extrabold text-on-surface-variant uppercase tracking-wider flex items-center gap-1.5">
                      <Folder className="w-4 h-4 text-primary fill-primary/5 animate-pulse" />
                      하위 가상 폴더 디렉터리 ({currentLevelFolders.length}개)
                    </h4>
                    <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
                      {currentLevelFolders.map((folder) => {
                        const folderName = folder.name;
                        const subDocsCount = documents.filter(d => isDescendantOrSelf(d.folderId, folder.folderId, folders)).length;
                        return (
                          <div 
                            key={`folder-grid-${folder.folderId}`}
                            onClick={() => setSelectedFolder(folder.folderId)}
                            className="group bg-surface-container-lowest hover:bg-primary/[0.02] p-4.5 rounded-2xl border border-outline-variant hover:border-primary/40 transition-all flex items-center justify-between cursor-pointer hover:shadow-md"
                          >
                            <div className="flex items-center gap-3 min-w-0">
                              <div className="p-2 bg-primary/5 group-hover:bg-primary/10 rounded-xl transition-colors shrink-0">
                                <Folder className="w-6 h-6 text-primary fill-primary/10 shrink-0" />
                              </div>
                              <div className="min-w-0">
                                <p className="font-extrabold text-xs text-on-surface truncate group-hover:text-primary transition-colors">
                                  {folderName}
                                </p>
                                <p className="text-[10px] text-outline font-sans">
                                  문서 {subDocsCount}개
                                </p>
                              </div>
                            </div>
                            <div className="flex items-center gap-1 shrink-0">
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleDeleteFolder(folder.folderId, folderName);
                                }}
                                className="p-1.5 rounded-lg opacity-0 group-hover:opacity-100 text-outline hover:text-rose-500 hover:bg-rose-50 transition-all cursor-pointer"
                                title="폴더 삭제"
                              >
                                <Trash2 className="w-3.5 h-3.5" />
                              </button>
                              <ChevronRight className="w-4 h-4 text-outline-variant group-hover:text-primary group-hover:translate-x-0.5 transition-all" />
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}

                {/* 1-B. 삭제된 폴더 (휴지통 탭 전용) */}
                {currentTab === "trash" && trashFolders && trashFolders.length > 0 && (
                  <div className="space-y-3.5">
                    <h4 className="text-xs font-extrabold text-on-surface-variant uppercase tracking-wider flex items-center gap-1.5">
                      <FolderClosed className="w-4 h-4 text-outline" />
                      삭제된 폴더 ({trashFolders.length}개)
                    </h4>
                    <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
                      {trashFolders.map((folder) => (
                        <div
                          key={`trash-folder-grid-${folder.folderId}`}
                          className="bg-surface-container-lowest p-4.5 rounded-2xl border border-outline-variant flex items-center justify-between"
                        >
                          <div className="flex items-center gap-3 min-w-0">
                            <div className="p-2 bg-outline-variant/20 rounded-xl shrink-0">
                              <FolderClosed className="w-6 h-6 text-outline shrink-0" />
                            </div>
                            <p className="font-extrabold text-xs text-on-surface truncate">{folder.name}</p>
                          </div>
                          <div className="flex items-center gap-1 shrink-0">
                            <button
                              type="button"
                              onClick={() => handleRestoreFolder(folder.folderId, folder.name)}
                              className="p-1.5 rounded-lg text-outline hover:text-primary hover:bg-primary/5 transition-all cursor-pointer"
                              title="폴더 복원"
                            >
                              <Undo2 className="w-3.5 h-3.5" />
                            </button>
                            <button
                              type="button"
                              onClick={() => handlePermanentDeleteFolder(folder.folderId, folder.name)}
                              className="p-1.5 rounded-lg text-outline hover:text-rose-500 hover:bg-rose-50 transition-all cursor-pointer"
                              title="폴더 영구 삭제"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* 2. Files Section */}
                <div className="space-y-3.5">
                  {currentTab === "mydrive" && !searchQuery && currentLevelFolders.length > 0 && (
                    <h4 className="text-xs font-extrabold text-on-surface-variant uppercase tracking-wider flex items-center gap-1.5 border-t border-outline-variant/10 pt-6">
                      <FileText className="w-4 h-4 text-outline" />
                      파일 문서 ({currentLevelDocuments.length}개)
                    </h4>
                  )}
                  
                  {currentLevelDocuments.length === 0 ? (
                    <div className="p-8 text-center bg-surface-container-lowest border border-outline-variant/50 rounded-2xl">
                      <p className="text-xs text-outline italic">이 디렉터리 레벨에 보관된 파일 문서가 존재하지 않습니다.</p>
                    </div>
                  ) : (
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                      {currentLevelDocuments.map((doc) => {
                        const isChecked = checkedDocIds.includes(doc.id);
                        return (
                          <div 
                            key={doc.id} 
                            className={`group bg-white p-5 rounded-2xl border transition-all flex flex-col justify-between relative ${
                              isChecked ? "border-primary bg-primary/[0.01] shadow-md shadow-primary/5" : "border-outline-variant hover:border-primary/50 hover:shadow-lg"
                            }`}
                            id={`grid-card-${doc.id}`}
                          >
                            {/* Grid Item Checkbox top-left */}
                            <div className="absolute top-4 left-4 z-10 flex items-center">
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setCheckedDocIds(prev => 
                                    prev.includes(doc.id) ? prev.filter(id => id !== doc.id) : [...prev, doc.id]
                                  );
                                }}
                                className={`w-5 h-5 rounded-md border flex items-center justify-center transition-all cursor-pointer ${
                                  isChecked 
                                    ? "bg-primary border-primary text-white shadow-sm" 
                                    : "bg-white border-outline-variant hover:border-outline text-transparent"
                                }`}
                              >
                                <Check className="w-3.5 h-3.5 stroke-[3]" />
                              </button>
                            </div>

                            <div>
                              <div className="flex justify-between items-start mb-4 gap-2 min-w-0 pl-7">
                                <div className="flex items-center gap-2.5 min-w-0 flex-1">
                                  {doc.fileType === "pdf" ? (
                                    <FileText className="w-10 h-10 text-rose-500 bg-rose-50 p-2 rounded-xl shrink-0" />
                                  ) : doc.fileType === "xlsx" ? (
                                    <FileSpreadsheet className="w-10 h-10 text-emerald-500 bg-emerald-50 p-2 rounded-xl shrink-0" />
                                  ) : (
                                    <FileText className="w-10 h-10 text-blue-500 bg-blue-50 p-2 rounded-xl shrink-0" />
                                  )}
                                  <div className="min-w-0 flex-1">
                                    <div className="flex items-center gap-1.5">
                                      <p className="font-bold text-body-sm text-on-surface leading-tight truncate cursor-text" title="더블클릭하여 이름 변경" onDoubleClick={() => handleRenameDocument(doc.id, doc.name)}>{doc.name}</p>
                                      <button
                                        type="button"
                                        onClick={(e) => {
                                          e.stopPropagation();
                                          handleToggleStar(doc.id);
                                        }}
                                        className="shrink-0 p-0.5 hover:bg-surface-container rounded-md transition-colors text-outline hover:text-amber-500 cursor-pointer"
                                        title={doc.star ? "중요 문서 해제" : "중요 문서 추가"}
                                      >
                                        <Star className={`w-3.5 h-3.5 ${doc.star ? "text-amber-500 fill-amber-400 stroke-amber-500" : "text-outline-variant group-hover:text-amber-500"}`} />
                                      </button>
                                    </div>
                                    <p className="text-[10px] text-outline mt-1 font-sans truncate">{formatBytes(doc.sizeBytes)} · {getFolderPath(doc.folderId, folders) || "미분류"}</p>
                                  </div>
                                </div>
                                <span className={`px-2 py-0.5 rounded text-[10px] font-extrabold shrink-0 ${
                                  doc.securityRank === "기밀" ? "bg-rose-50 text-rose-600 border border-rose-100 animate-pulse" : "bg-cyan-50 text-cyan-600 border border-cyan-100"
                                }`}>
                                  {doc.securityRank}
                                </span>
                              </div>

                              {/* AI Summary block */}
                              <p className="text-[11px] text-on-surface-variant line-clamp-2 leading-relaxed bg-surface-container-low/50 p-3 rounded-xl mb-4 font-sans border border-outline-variant/20" id={`summary-box-${doc.id}`}>
                                {doc.summary}
                              </p>
                              
                              {/* Metadata Display for docType and entities */}
                              {(doc.docType || (doc.entities && (doc.entities.dates?.length || doc.entities.people?.length || doc.entities.amounts?.length || doc.entities.project))) && (
                                <div className="text-[10px] bg-secondary/[0.02] border border-secondary/10 rounded-xl p-2.5 mb-3 space-y-1 text-on-surface">
                                  {doc.docType && (
                                    <div className="flex justify-between items-center border-b border-outline-variant/30 pb-1 mb-1">
                                      <span className="text-outline font-extrabold text-[9px] uppercase tracking-wider">문서 분류 유형</span>
                                      <span className="font-extrabold text-secondary bg-secondary/10 px-1.5 py-0.5 rounded text-[9.5px]">{doc.docType}</span>
                                    </div>
                                  )}
                                  {doc.entities && (
                                    <div className="space-y-1 text-[9.5px]">
                                      {doc.entities.project && (
                                        <div className="flex gap-1.5 truncate">
                                          <span className="text-outline shrink-0 font-bold">프로젝트:</span>
                                          <span className="font-extrabold text-primary truncate">{doc.entities.project}</span>
                                        </div>
                                      )}
                                      {doc.entities.dates && doc.entities.dates.length > 0 && (
                                        <div className="flex gap-1.5 truncate">
                                          <span className="text-outline shrink-0 font-bold">주요 일정:</span>
                                          <span className="font-semibold truncate text-on-surface-variant">{doc.entities.dates.join(", ")}</span>
                                        </div>
                                      )}
                                      {doc.entities.people && doc.entities.people.length > 0 && (
                                        <div className="flex gap-1.5 truncate">
                                          <span className="text-outline shrink-0 font-bold">관련 인물:</span>
                                          <span className="font-semibold truncate text-on-surface-variant">{doc.entities.people.join(", ")}</span>
                                        </div>
                                      )}
                                      {doc.entities.amounts && doc.entities.amounts.length > 0 && (
                                        <div className="flex gap-1.5 truncate">
                                          <span className="text-outline shrink-0 font-bold">금액 정보:</span>
                                          <span className="font-semibold truncate text-emerald-600 font-mono">{doc.entities.amounts.join(", ")}</span>
                                        </div>
                                      )}
                                    </div>
                                  )}
                                </div>
                              )}
                            </div>

                            <div>
                              <div className="flex flex-wrap gap-1.5 mb-4">
                                {doc.tags.map((tag, idx) => (
                                  <span key={idx} className="bg-primary/5 px-2 py-0.5 rounded text-[10px] font-extrabold text-primary border border-primary/10">
                                    #{tag}
                                  </span>
                                ))}
                              </div>

                              <div className="flex gap-2 border-t border-outline-variant/30 pt-4 mt-2">
                                <button 
                                  onClick={() => onNavigateToChat([doc.id])}
                                  className="flex-1 py-2 bg-secondary text-white rounded-xl text-[11px] font-extrabold hover:bg-opacity-95 transition-colors cursor-pointer flex items-center justify-center gap-1 shadow-sm"
                                >
                                  <Sparkles className="w-3.5 h-3.5 fill-white/20 animate-pulse" /> RAG 대화
                                </button>
                                <button 
                                  onClick={() => {
                                    setMovingDocIds([doc.id]);
                                    setMoveTargetFolder(doc.folderId);
                                    setIsMoveModalOpen(true);
                                  }}
                                  className="p-2 border border-outline-variant text-outline hover:text-primary hover:border-primary/40 rounded-xl hover:bg-primary/5 transition-colors cursor-pointer"
                                  title="폴더로 이동"
                                >
                                  <FolderInput className="w-4 h-4" />
                                </button>
                                <button
                                    onClick={() => setDetailFileId(Number(doc.id))}
                                    className="p-2 border border-outline-variant text-outline hover:text-on-surface rounded-xl hover:bg-surface-container transition-colors cursor-pointer"
                                    title="상세 정보"
                                >
                                  <Info className="w-4 h-4" />
                                </button>
                                <button
                                    onClick={() => downloadFile(Number(doc.id), doc.name).catch(() => alert("다운로드에 실패했습니다."))}
                                  className="p-2 border border-outline-variant text-outline hover:text-on-surface rounded-xl hover:bg-surface-container transition-colors cursor-pointer"
                                  title="다운로드"
                                >
                                  <Download className="w-4 h-4" />
                                </button>
                              </div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              </div>
            ) : (
              /* List Layout view */
              <div className="overflow-x-auto w-full border border-outline-variant/50 rounded-2xl bg-surface-bright" id="vault-table-wrapper">
                <table className="min-w-[1000px] w-full text-left table-fixed" id="vault-table">
                  <colgroup>
                    <col className="w-12" />
                    <col className="w-[32%] min-w-[280px]" />
                    <col className="w-[20%] min-w-[180px]" />
                    <col className="w-[14%] min-w-[120px]" />
                    <col className="w-[10%] min-w-[90px]" />
                    <col className="w-[12%] min-w-[110px]" />
                    <col className="w-[12%] min-w-[120px]" />
                  </colgroup>
                  <thead>
                    <tr className="bg-surface-container-low text-outline text-[11px] font-extrabold border-b border-outline-variant uppercase tracking-wider">
                      <th className="px-6 py-4 text-center whitespace-nowrap">
                        <button
                          type="button"
                          onClick={() => {
                            const isAllFilteredChecked = currentLevelDocuments.length > 0 && currentLevelDocuments.every(d => checkedDocIds.includes(d.id));
                            if (isAllFilteredChecked) {
                              setCheckedDocIds(prev => prev.filter(id => !currentLevelDocuments.some(fd => fd.id === id)));
                            } else {
                              const allIds = currentLevelDocuments.map(d => d.id);
                              setCheckedDocIds(prev => Array.from(new Set([...prev, ...allIds])));
                            }
                          }}
                          className={`w-4.5 h-4.5 rounded border flex items-center justify-center mx-auto cursor-pointer transition-all ${
                            currentLevelDocuments.length > 0 && currentLevelDocuments.every(d => checkedDocIds.includes(d.id))
                              ? "bg-primary border-primary text-white"
                              : "bg-white border-outline-variant hover:border-outline text-transparent"
                          }`}
                        >
                          <Check className="w-3 h-3 stroke-[3]" />
                        </button>
                      </th>
                      <th className="px-6 py-4 whitespace-nowrap">파일명 및 경로</th>
                      <th className="px-6 py-4 whitespace-nowrap">AI 추출 태그</th>
                      <th className="px-6 py-4 whitespace-nowrap">최종 수정일</th>
                      <th className="px-6 py-4 whitespace-nowrap">용량</th>
                      <th className="px-6 py-4 whitespace-nowrap">보안 상태</th>
                      <th className="px-6 py-4 text-center whitespace-nowrap">작업</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-outline-variant" id="vault-table-body">
                    {/* 1. Folders Row Section */}
                    {currentTab === "mydrive" && !searchQuery && currentLevelFolders.map((folder) => {
                      const folderName = folder.name;
                      return (
                        <tr 
                           key={`folder-row-${folder.folderId}`} 
                          onClick={() => setSelectedFolder(folder.folderId)}
                          className="hover:bg-primary/[0.01] cursor-pointer transition-colors group"
                        >
                          <td className="px-6 py-4 text-center">
                            <Folder className="w-4 h-4 text-primary fill-primary/5 mx-auto shrink-0" />
                          </td>
                          <td className="px-6 py-4">
                            <div className="flex items-center gap-3">
                              <Folder className="w-5 h-5 text-primary fill-primary/10 shrink-0" />
                              <div className="truncate">
                                <p className="font-bold text-xs text-on-surface leading-tight group-hover:text-primary transition-colors truncate">{folderName}</p>
                                <p className="text-[10px] text-outline mt-1 font-sans truncate">📂 {getFolderPath(folder.folderId, folders) || "미분류"}</p>
                              </div>
                            </div>
                          </td>
                          <td className="px-6 py-4 text-[10px] font-extrabold text-outline font-sans whitespace-nowrap">
                            가상 폴더 디렉터리
                          </td>
                          <td className="px-6 py-4 text-xs font-semibold text-outline font-sans whitespace-nowrap">
                            -
                          </td>
                          <td className="px-6 py-4 text-xs font-semibold text-outline font-sans whitespace-nowrap">
                            -
                          </td>
                          <td className="px-6 py-4 whitespace-nowrap">
                            <span className="inline-flex items-center gap-1 text-[10px] font-extrabold text-outline bg-surface-container px-2 py-0.5 rounded whitespace-nowrap">
                              폴더
                            </span>
                          </td>
                          <td className="px-6 py-4 text-center whitespace-nowrap">
                            <div className="flex items-center justify-center gap-2">
                              <button 
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setSelectedFolder(folder.folderId);
                                }}
                                className="px-2.5 py-1.5 bg-primary/10 text-primary text-[10px] font-extrabold rounded-lg hover:bg-primary/20 transition-all cursor-pointer whitespace-nowrap"
                              >
                                폴더 열기
                              </button>
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleDeleteFolder(folder.folderId, folderName);
                                }}
                                className="p-1.5 rounded-lg text-outline hover:text-rose-500 hover:bg-rose-50 transition-all cursor-pointer"
                                title="폴더 삭제"
                              >
                                <Trash2 className="w-3.5 h-3.5" />
                              </button>
                            </div>
                          </td>
                        </tr>
                      );
                    })}

                    {/* 1-B. 삭제된 폴더 Row Section (휴지통 탭 전용) */}
                    {currentTab === "trash" && trashFolders && trashFolders.map((folder) => (
                      <tr key={`trash-folder-row-${folder.folderId}`} className="hover:bg-surface-container-low transition-colors group">
                        <td className="px-6 py-4 text-center">
                          <FolderClosed className="w-4 h-4 text-outline mx-auto shrink-0" />
                        </td>
                        <td className="px-6 py-4">
                          <div className="flex items-center gap-3">
                            <FolderClosed className="w-5 h-5 text-outline shrink-0" />
                            <p className="font-bold text-xs text-on-surface leading-tight truncate">{folder.name}</p>
                          </div>
                        </td>
                        <td className="px-6 py-4 text-[10px] font-extrabold text-outline font-sans whitespace-nowrap">
                          가상 폴더 디렉터리
                        </td>
                        <td className="px-6 py-4 text-xs font-semibold text-outline font-sans whitespace-nowrap">-</td>
                        <td className="px-6 py-4 text-xs font-semibold text-outline font-sans whitespace-nowrap">-</td>
                        <td className="px-6 py-4 whitespace-nowrap">
                          <span className="inline-flex items-center gap-1 text-[10px] font-extrabold text-outline bg-surface-container px-2 py-0.5 rounded whitespace-nowrap">
                            폴더
                          </span>
                        </td>
                        <td className="px-6 py-4 text-center whitespace-nowrap">
                          <div className="flex items-center justify-center gap-2">
                            <button
                              type="button"
                              onClick={() => handleRestoreFolder(folder.folderId, folder.name)}
                              className="px-2.5 py-1.5 bg-primary/10 text-primary text-[10px] font-extrabold rounded-lg hover:bg-primary/20 transition-all cursor-pointer whitespace-nowrap flex items-center gap-1"
                            >
                              <Undo2 className="w-3 h-3" /> 복원
                            </button>
                            <button
                              type="button"
                              onClick={() => handlePermanentDeleteFolder(folder.folderId, folder.name)}
                              className="p-1.5 rounded-lg text-outline hover:text-rose-500 hover:bg-rose-50 transition-all cursor-pointer"
                              title="폴더 영구 삭제"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}

                    {/* 2. Files Row Section */}
                    {currentLevelDocuments.map((doc) => {
                      const isChecked = checkedDocIds.includes(doc.id);
                      return (
                        <tr key={doc.id} className={`hover:bg-surface-container-low transition-colors group ${isChecked ? "bg-primary/[0.01]" : ""}`}>
                          <td className="px-6 py-4 text-center">
                            <button
                              type="button"
                              onClick={() => {
                                setCheckedDocIds(prev => 
                                  prev.includes(doc.id) ? prev.filter(id => id !== doc.id) : [...prev, doc.id]
                                );
                              }}
                              className={`w-4.5 h-4.5 rounded border flex items-center justify-center mx-auto cursor-pointer transition-all ${
                                isChecked
                                  ? "bg-primary border-primary text-white"
                                  : "bg-white border-outline-variant hover:border-outline text-transparent group-hover:border-outline"
                              }`}
                            >
                              <Check className="w-3 h-3 stroke-[3]" />
                            </button>
                          </td>
                          <td className="px-6 py-4">
                            <div className="flex items-center gap-3">
                              {/* Star button inside list view */}
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleToggleStar(doc.id);
                                }}
                                className="p-1 rounded-md hover:bg-surface-container transition-colors cursor-pointer text-outline hover:text-amber-500 shrink-0"
                                title={doc.star ? "중요 문서 해제" : "중요 문서 추가"}
                              >
                                <Star className={`w-3.5 h-3.5 ${doc.star ? "text-amber-500 fill-amber-400 stroke-amber-500" : "text-outline-variant group-hover:text-amber-500"}`} />
                              </button>

                              {doc.fileType === "pdf" ? (
                                <FileText className="w-6 h-6 text-rose-500 shrink-0" />
                              ) : doc.fileType === "xlsx" ? (
                                <FileSpreadsheet className="w-6 h-6 text-emerald-500 shrink-0" />
                              ) : (
                                <FileText className="w-6 h-6 text-blue-500 shrink-0" />
                              )}
                              <div className="truncate flex-1">
                                <p className="font-bold text-xs text-on-surface leading-tight truncate cursor-text" title="더블클릭하여 이름 변경" onDoubleClick={() => handleRenameDocument(doc.id, doc.name)}>{doc.name}</p>
                                <p className="text-[10px] text-outline mt-1 font-sans truncate">
                                  {getFolderPath(doc.folderId, folders) || "미분류"} · {doc.ownerName}
                                </p>
                                {doc.docType && (
                                  <span className="inline-block mt-1 px-1.5 py-0.5 bg-secondary/10 text-secondary text-[9px] font-extrabold rounded whitespace-nowrap">
                                    {doc.docType}
                                  </span>
                                )}
                              </div>
                            </div>
                          </td>
                          <td className="px-6 py-4">
                            <div className="flex flex-wrap gap-1">
                              {doc.tags.map((tag, idx) => (
                                <span key={idx} className="px-1.5 py-0.5 bg-primary/5 text-primary text-[9.5px] font-extrabold rounded border border-primary/10 whitespace-nowrap">
                                  #{tag}
                                </span>
                              ))}
                            </div>
                          </td>
                          <td className="px-6 py-4 text-xs font-semibold text-on-surface-variant font-sans whitespace-nowrap">
                            {formatDateTime(doc.modifiedAt)}
                          </td>
                          <td className="px-6 py-4 text-xs font-semibold text-on-surface-variant font-sans whitespace-nowrap">
                            {formatBytes(doc.sizeBytes)}
                          </td>
                          <td className="px-6 py-4 whitespace-nowrap">
                            <span className={`inline-flex items-center gap-1 text-[11px] font-extrabold whitespace-nowrap ${
                              doc.securityRank === "기밀" ? "text-error" : "text-secondary"
                            }`}>
                              <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${doc.securityRank === "기밀" ? "bg-error animate-ping" : "bg-secondary"}`}></span>
                              {doc.securityRank}
                            </span>
                          </td>
                          <td className="px-6 py-4 text-center whitespace-nowrap">
                            <div className="flex items-center justify-center gap-1.5">
                              <button 
                                onClick={() => onNavigateToChat([doc.id])}
                                className="px-2.5 py-1.5 bg-secondary text-white text-[10px] font-extrabold rounded-lg hover:bg-opacity-95 shadow-sm transition-all cursor-pointer flex items-center gap-1"
                              >
                                <Sparkles className="w-3 h-3 fill-white/10" /> RAG
                              </button>
                              <button 
                                onClick={() => {
                                  setMovingDocIds([doc.id]);
                                  setMoveTargetFolder(doc.folderId);
                                  setIsMoveModalOpen(true);
                                }}
                                className="p-1.5 hover:bg-primary/5 rounded-lg text-outline hover:text-primary border border-transparent hover:border-primary/20 transition-all cursor-pointer"
                                title="폴더로 이동"
                              >
                                <FolderInput className="w-4 h-4" />
                              </button>
                              <button
                                  onClick={() => setDetailFileId(Number(doc.id))}
                                  className="p-1.5 hover:bg-surface-container rounded-lg text-outline hover:text-on-surface cursor-pointer"
                                  title="상세 정보"
                              >
                                <Info className="w-4 h-4" />
                              </button>
                              <button
                                  onClick={() => downloadFile(Number(doc.id), doc.name).catch(() => alert("다운로드에 실패했습니다."))}
                                className="p-1.5 hover:bg-surface-container rounded-lg text-outline hover:text-on-surface cursor-pointer"
                                title="다운로드"
                              >
                                <Download className="w-4 h-4" />
                              </button>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </section>
      </div>

      {/* 1) AI 스마트 정리중 로딩 오버레이 모달 */}
      <AnimatePresence>
        {isOrganizing && (
          <div className="fixed inset-0 z-[110] flex items-center justify-center bg-black/60 backdrop-blur-md">
            <motion.div 
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              className="bg-white rounded-3xl p-8 max-w-md w-full border border-outline-variant shadow-2xl text-center space-y-6"
            >
              <div className="relative w-20 h-20 mx-auto">
                <div className="absolute inset-0 rounded-full border-4 border-secondary/15 animate-pulse"></div>
                <div className="absolute inset-0 rounded-full border-4 border-transparent border-t-primary animate-spin"></div>
                <div className="absolute inset-0 flex items-center justify-center text-primary">
                  <Sparkles className="w-8 h-8 fill-primary/10 animate-pulse" />
                </div>
              </div>

              <div className="space-y-2">
                <h4 className="text-lg font-bold text-on-surface font-sans">AI 스마트 정리 기동 중...</h4>
                <p className="text-xs text-outline max-w-xs mx-auto leading-relaxed">
                  인공지능이 보관함 내 문서 정보와 텍스트 의미적 가독 유사도를 진단하여 폴더 체계를 통합 개편 중입니다.
                </p>
              </div>

              {/* Progressive text indicators */}
              <div className="bg-surface-container-low p-4 rounded-2xl border border-outline-variant/30 space-y-3">
                <div className="flex items-center gap-2.5 text-left text-xs">
                  <div className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold ${
                    organizeStep >= 1 ? "bg-primary text-white" : "bg-outline-variant/30 text-outline"
                  }`}>
                    {organizeStep > 1 ? "✓" : "1"}
                  </div>
                  <span className={`${organizeStep === 1 ? "font-bold text-primary" : "text-outline"}`}>
                    전체 보관함 메타데이터 인덱싱
                  </span>
                </div>

                <div className="flex items-center gap-2.5 text-left text-xs">
                  <div className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold ${
                    organizeStep >= 2 ? "bg-primary text-white" : "bg-outline-variant/30 text-outline"
                  }`}>
                    {organizeStep > 2 ? "✓" : "2"}
                  </div>
                  <span className={`${organizeStep === 2 ? "font-bold text-primary" : "text-outline"}`}>
                    LLM 의미적 연관도 분석 및 임베딩 군집화
                  </span>
                </div>

                <div className="flex items-center gap-2.5 text-left text-xs">
                  <div className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold ${
                    organizeStep >= 3 ? "bg-primary text-white" : "bg-outline-variant/30 text-outline"
                  }`}>
                    {organizeStep > 3 ? "✓" : "3"}
                  </div>
                  <span className={`${organizeStep === 3 ? "font-bold text-primary animate-pulse" : "text-outline"}`}>
                    표준 부서 분류 폴더 생성 및 재정비 적용
                  </span>
                </div>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>

      {/* 2) AI 스마트 정리 완료 미리보기 및 대조 검토 모달 */}
      <AnimatePresence>
        {organizeResult && (
          <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm">
            <motion.div 
              initial={{ scale: 0.95, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.95, opacity: 0 }}
              className="bg-white rounded-3xl w-full max-w-3xl overflow-hidden shadow-2xl border border-outline-variant relative flex flex-col h-[85vh]"
              id="organize-result-panel"
            >
              {(() => {
                const { appliedMappings, tempIdsToCreate } = getOrganizeApplyPreview(organizeResult, applyResult);
                const heldCount = applyResult?.held.length ?? 0;
                return (
                <>
              {/* Header */}
              <div className="px-8 py-5 border-b border-outline-variant flex justify-between items-center bg-surface-container-lowest shrink-0">
                <div className="flex items-center gap-2 text-primary">
                  <Sparkles className="w-5.5 h-5.5 text-secondary fill-secondary/10" />
                  <div>
                    <h3 className="text-lg font-bold text-on-surface">
                      {applyResult ? "AI 스마트 정리 적용 결과" : "AI 스마트 정리 미리보기"}
                    </h3>
                    <p className="text-[10.5px] text-outline font-medium">
                      {applyResult
                        ? "실제로 반영된 항목과 확신도 미달로 보류된 항목입니다."
                        : "기존 가상 폴더 구조와 AI가 의미론적으로 추천하는 신규 배치안을 비교해 보세요."}
                    </p>
                  </div>
                </div>
                <button
                  onClick={closeOrganizeModal}
                  className="p-2 hover:bg-surface-container rounded-full text-outline transition-colors cursor-pointer"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              {/* Scrollable Content Area */}
              <div className="p-8 space-y-6 overflow-y-auto flex-1 custom-scrollbar bg-surface-bright">
                {/* Top Summary */}
                <div className="flex flex-col md:flex-row gap-4 items-start md:items-center justify-between p-4 bg-gradient-to-br from-primary/[0.03] to-secondary/[0.03] rounded-2xl border border-outline-variant/60">
                  <div className="space-y-1">
                    <span className="inline-flex items-center gap-1.5 px-3 py-1 bg-secondary/10 text-secondary text-[11px] font-extrabold rounded-full tracking-wider uppercase border border-secondary/15">
                      {applyResult ? "✅ 반영 완료" : "💡 AI 폴더 구조 재편 제안"}
                    </span>
                    <p className="text-body-sm font-extrabold text-on-surface">
                      {applyResult ? "적용 결과" : "새 폴더 생성 및 파일 이동 제안"}
                    </p>
                  </div>
                  <div className="text-[11px] text-outline font-sans bg-white px-3 py-1.5 rounded-lg border border-outline-variant/40">
                    새 폴더 <span className="font-bold text-primary">{tempIdsToCreate.size}개</span> ·
                    파일 <span className="font-bold text-primary">{appliedMappings.length}건</span>{" "}
                    {applyResult ? "반영됨" : "이동/이름변경 제안"}
                    {heldCount > 0 && (
                      <> · 보류 <span className="font-bold text-amber-600">{heldCount}건</span></>
                    )}
                  </div>
                </div>

                {/* confidence가 하나라도 있으면(적용 전/후 공통), 민감도 미달 항목이 어떻게 되는지 안내한다. */}
                {organizeResult.mappings.some((m) => m.confidence != null) && (
                  <div className="p-3 bg-secondary/5 border border-secondary/20 rounded-lg text-xs leading-relaxed text-on-surface">
                    ℹ️ {applyResult ? (
                      <>
                        확신도가 자동 분류 민감도보다 낮았던 항목은 반영되지 않고 원래 위치에 그대로 남았습니다.
                        <br />
                        (아래 목록에 보류로 표시)
                      </>
                    ) : (
                      <>
                        확신도가 설정에서 지정한 자동 분류 민감도보다 낮은 항목은 적용해도 자동으로 이동되지 않고 보류됩니다.
                        <br />
                        (설정 &gt; AI 설정에서 민감도 조정 가능)
                      </>
                    )}
                  </div>
                )}

                {/* New folders list */}
                <div className="space-y-3">
                  <h4 className="font-extrabold text-xs text-on-surface uppercase tracking-wider mb-2">
                    {applyResult ? "생성된 폴더" : "새로 생성될 폴더"}
                  </h4>

                  {organizeResult.newFolders.length === 0 ? (
                    <div className="p-4 bg-surface-container-low/40 border border-outline-variant/20 rounded-xl text-[11px] text-outline">
                      새로 생성할 폴더는 없습니다. 기존 폴더 안에서 파일 이동/이름변경만 제안되었습니다.
                    </div>
                  ) : (
                    <div className="space-y-2.5">
                      {organizeResult.newFolders.map((folder) => {
                        const willCreate = tempIdsToCreate.has(folder.tempId);
                        return (
                        <div
                          key={folder.tempId}
                          className={`flex items-center gap-3 p-3.5 rounded-xl relative overflow-hidden border ${
                            willCreate
                              ? "bg-secondary/5 border-secondary/20"
                              : "bg-surface-container-low/30 border-outline-variant/20 opacity-60"
                          }`}
                        >
                          {willCreate && (
                            <div className="absolute right-0 bottom-0 opacity-[0.03] pointer-events-none text-secondary">
                              <Sparkles className="w-10 h-10" />
                            </div>
                          )}
                          <span
                            className={`text-[9px] font-extrabold px-1.5 py-0.5 rounded shrink-0 ${
                              willCreate ? "bg-secondary text-white" : "bg-outline-variant text-outline"
                            }`}
                          >
                            {willCreate ? "신규" : applyResult ? "미생성" : "보류 예정"}
                          </span>
                          <p className={`text-[11px] font-extrabold truncate ${willCreate ? "text-secondary" : "text-outline"}`}>
                            📂 {resolveProposedFolderPath(folder, organizeResult.newFolders)}
                          </p>
                        </div>
                        );
                      })}
                    </div>
                  )}
                </div>

                {/* File mapping detail — 파일별로 현재 위치/이름 → 목표 위치/이름을 대조해서 보여준다. */}
                <div className="space-y-3">
                  <h4 className="font-extrabold text-xs text-on-surface uppercase tracking-wider mb-2">
                    파일 이동/이름변경 상세 ({organizeResult.mappings.length}건)
                  </h4>

                  {organizeResult.mappings.length === 0 ? (
                    <div className="p-4 bg-surface-container-low/40 border border-outline-variant/20 rounded-xl text-[11px] text-outline">
                      이동하거나 이름을 바꿀 파일이 없습니다.
                    </div>
                  ) : (
                    <div className="space-y-2 max-h-64 overflow-y-auto custom-scrollbar pr-1">
                      {organizeResult.mappings.map((mapping) => {
                        const { currentName, currentPath, targetPath, newName, confidence } =
                            resolveMappingDisplay(mapping, organizeResult.newFolders);
                        // 적용 후엔 실제 held 응답으로, 적용 전엔 confidence/민감도로 예측한다 —
                        // 어느 쪽이든 "보류"면 AI가 제안한 대상이 뭐였든 상관없이 "미분류"로 보여준다.
                        // 이 앱에서 폴더가 지정 안 된 파일은 원래 "미분류"로 표시되는데, 확신도가
                        // 낮아 반영이 안 되면(또는 안 될 예정이면) 결국 그 상태와 같기 때문이다.
                        const isHeld = applyResult
                          ? applyResult.held.some((h) => h.fileId === mapping.fileId)
                          : !appliedMappings.some((m) => m.fileId === mapping.fileId);
                        return (
                          <div
                            key={mapping.fileId}
                            className={`p-3 bg-white border rounded-xl text-[11px] ${
                              isHeld ? "border-amber-300 bg-amber-50/40" : "border-outline-variant/40"
                            }`}
                          >
                            <div className="flex items-center justify-between gap-2">
                              <p className="font-extrabold text-on-surface truncate">
                                📄 {currentName}
                                {newName && newName !== currentName && (
                                  <span className="text-secondary"> → {newName}</span>
                                )}
                              </p>
                              <div className="flex items-center gap-1.5 shrink-0">
                                {/* AI 확신도 — apply 시 이 값이 자동 분류 민감도보다 낮으면 실제로는
                                    반영되지 않고 보류된다. */}
                                {confidence != null && (
                                  <span className="text-[9px] font-bold text-outline bg-surface-container-low px-1.5 py-0.5 rounded">
                                    확신도 {Math.round(confidence * 100)}%
                                  </span>
                                )}
                                {isHeld ? (
                                  <span className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-amber-100 text-amber-700">
                                    {applyResult ? "보류됨" : "보류 예정"}
                                  </span>
                                ) : applyResult ? (
                                  <span className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-primary/10 text-primary">
                                    반영됨
                                  </span>
                                ) : null}
                              </div>
                            </div>
                            <p className="text-outline mt-1">
                              {currentPath || "미분류"} <span className="mx-1">→</span>{" "}
                              <span className={`font-bold ${isHeld ? "text-amber-700" : "text-primary"}`}>
                                {isHeld ? "미분류" : targetPath}
                              </span>
                            </p>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              </div>

              {/* Action buttons footer */}
              <div className="p-6 border-t border-outline-variant/30 bg-surface-container-lowest shrink-0 flex items-center justify-between">
                {applyResult ? (
                  <div className="w-full flex justify-end">
                    <button
                      onClick={closeOrganizeModal}
                      className="px-6 py-3 bg-primary hover:bg-opacity-95 text-white font-extrabold text-xs rounded-xl shadow-lg shadow-primary/10 transition-all cursor-pointer"
                    >
                      확인
                    </button>
                  </div>
                ) : (
                  <>
                    <button
                      onClick={closeOrganizeModal}
                      className="px-5 py-3 bg-white border border-outline-variant/50 hover:bg-surface-container rounded-xl font-bold text-xs text-outline hover:text-on-surface transition-all cursor-pointer"
                    >
                      분석 정리 취소
                    </button>

                    <div className="flex items-center gap-3">
                      <span className="text-[10px] text-outline hidden md:inline-block">
                        * 실제 폴더 생성 및 파일 이동/이름변경이 반영됩니다 (되돌리기 미지원)
                      </span>
                      <button
                        onClick={handleApplyOrganization}
                        disabled={isApplyingOrganize}
                        className="px-6 py-3 bg-primary hover:bg-opacity-95 text-white font-extrabold text-xs rounded-xl shadow-lg shadow-primary/10 flex items-center gap-1.5 transition-all cursor-pointer hover:scale-[1.01] active:scale-95 disabled:opacity-60 disabled:cursor-not-allowed disabled:hover:scale-100"
                      >
                        <Sparkles className="w-4 h-4 text-secondary-container fill-secondary-container/20 animate-pulse" />
                        {isApplyingOrganize ? "적용 중..." : "추천 폴더 배치 적용하기"}
                      </button>
                    </div>
                  </>
                )}
              </div>
                </>
                );
              })()}
            </motion.div>
          </div>
        )}
      </AnimatePresence>

      {/* 3) AI 스마트 업로드 자동 배치 결과 모달 */}
      <AnimatePresence>
        {showAutoResultModal && autoResultData && (
          <div className="fixed inset-0 z-[110] flex items-center justify-center bg-black/60 backdrop-blur-md">
            <motion.div 
              initial={{ scale: 0.95, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.95, opacity: 0 }}
              className="bg-white rounded-3xl w-full max-w-lg p-8 shadow-2xl border border-outline-variant relative overflow-hidden"
              id="smart-upload-result-modal"
            >
              {/* Background ambient blur */}
              <div className="absolute right-0 top-0 w-36 h-36 bg-secondary/10 blur-3xl rounded-full pointer-events-none"></div>
              
              <div className="flex flex-col items-center text-center space-y-6">
                <div className="w-14 h-14 bg-secondary/15 rounded-2xl flex items-center justify-center text-secondary">
                  <Sparkles className="w-7 h-7 fill-secondary/15 animate-bounce-subtle" />
                </div>

                <div className="space-y-1">
                  <span className="px-2.5 py-0.5 bg-secondary/10 text-secondary text-[10px] font-extrabold rounded-full tracking-widest uppercase animate-pulse">
                    AI 실시간 자동 분류 정리 완료
                  </span>
                  <h3 className="text-xl font-bold text-on-surface">문서가 자동 정리 배치되었습니다!</h3>
                </div>

                {/* Visual connection animation container */}
                <div className="w-full bg-surface-container-low/50 p-6 rounded-2xl border border-outline-variant/30 space-y-4 relative">
                  <div className="flex justify-between items-center relative">
                    {/* Source file representation */}
                    <div className="flex flex-col items-center gap-1.5 w-1/3">
                      <div className="w-12 h-12 bg-white rounded-xl border border-outline-variant shadow-sm flex items-center justify-center text-rose-500">
                        <FileText className="w-6 h-6" />
                      </div>
                      <span className="text-[10px] font-bold text-on-surface truncate w-full px-1">{autoResultData.fileName}</span>
                      <span className="text-[9px] text-outline font-sans">방금 업로드</span>
                    </div>

                    {/* Animated connecting line */}
                    <div className="flex-1 flex flex-col items-center relative py-4">
                      <div className="h-[2px] bg-gradient-to-r from-rose-400 to-secondary w-full relative">
                        {/* Spark animation traveling on line */}
                        <span className="absolute -top-[3px] left-0 w-2 h-2 rounded-full bg-secondary animate-pulse" style={{
                          animation: "bounceSubtle 1.5s infinite"
                        }}></span>
                      </div>
                      <span className="text-[10px] text-secondary font-extrabold bg-white px-2 py-0.5 rounded-full border border-secondary/15 -mt-2 shadow-sm">
                        AI 매칭 배치
                      </span>
                    </div>

                    {/* Destination Folder representation */}
                    <div className="flex flex-col items-center gap-1.5 w-1/3">
                      <div className="w-12 h-12 bg-secondary text-white rounded-xl shadow-md flex items-center justify-center">
                        <Folder className="w-6 h-6 fill-white/10" />
                      </div>
                      <span className="text-[10px] font-extrabold text-secondary truncate w-full px-1">
                        [{autoResultData.targetFolder}]
                      </span>
                      <span className="text-[9px] text-outline font-sans">배치 정돈 완료</span>
                    </div>
                  </div>

                  <div className="h-px bg-outline-variant/30"></div>

                  {/* Generated metadata summary inside card */}
                  <div className="text-left space-y-2 text-xs">
                    <p className="text-outline font-extrabold text-[10px] tracking-wider uppercase">AI 실시간 해독 및 검증 데이터</p>
                    <p className="text-on-surface font-semibold leading-relaxed bg-white p-3 rounded-xl border border-outline-variant/30">
                      💡 {autoResultData.summary}
                    </p>
                    <div className="flex justify-between items-center pt-1.5 text-[11px]">
                      <span className="text-outline font-bold">보안 조치 수준:</span>
                      <span className={`px-2 py-0.5 rounded font-extrabold ${
                        autoResultData.security === "기밀" ? "bg-rose-50 text-rose-600 border border-rose-100" : "bg-cyan-50 text-cyan-600"
                      }`}>{autoResultData.security}</span>
                    </div>
                    <div className="flex flex-wrap gap-1 pt-1.5">
                      {autoResultData.tags.map((t, idx) => (
                        <span key={idx} className="bg-primary/5 text-primary text-[9px] font-bold px-2 py-0.5 rounded">
                          #{t}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>

                {/* Confirm buttons */}
                <div className="flex gap-3 w-full">
                  <button 
                    onClick={() => {
                      setSelectedFolder(autoResultData.targetFolderId);
                      setShowAutoResultModal(false);
                    }}
                    className="flex-1 py-3 bg-secondary text-white rounded-xl font-bold text-xs hover:bg-opacity-95 transition-all cursor-pointer shadow-md shadow-secondary/10 flex items-center justify-center gap-1"
                  >
                    이동해서 배치 확인 <ChevronRight className="w-4 h-4" />
                  </button>
                  <button 
                    onClick={() => setShowAutoResultModal(false)}
                    className="px-5 py-3 bg-surface-container text-on-surface hover:bg-surface-container-high rounded-xl font-bold text-xs transition-colors cursor-pointer border border-outline-variant/30"
                  >
                    닫기
                  </button>
                </div>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>

      <FileDetailPanel
          fileId={detailFileId}
          folders={folders}
          onClose={() => setDetailFileId(null)}
          onTagsChanged={(id, tags) => onUpdateDocuments((prev) => prev.map((d) => (d.id === String(id) ? { ...d, tags } : d)))}
          onDocumentTypeChanged={(id, documentType) => onUpdateDocuments((prev) => prev.map((d) => (d.id === String(id) ? { ...d, documentType } : d)))}
      />

      {/* Dynamic Upload Modal Form */}
      <AnimatePresence>
        {isUploadOpen && (
          <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm" id="upload-dialog-overlay">
            <motion.div 
              initial={{ scale: 0.95, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.95, opacity: 0 }}
              className="bg-white rounded-3xl w-full max-w-2xl overflow-hidden shadow-2xl border border-outline-variant relative"
              id="upload-dialog-panel"
            >
              <div className="px-8 py-5 border-b border-outline-variant flex justify-between items-center bg-surface-container-lowest">
                <div className="flex items-center gap-2 text-primary">
                  <Sparkles className="w-5 h-5 fill-primary/10 animate-pulse" />
                  <h3 className="text-xl font-bold">
                    {isSpecialUploadMode ? "AI 스마트 자동 정리 업로드" : "지능형 새 문서 추가"}
                  </h3>
                </div>
                <button 
                  onClick={() => setIsUploadOpen(false)}
                  className="p-2 hover:bg-surface-container rounded-full text-outline hover:text-on-surface transition-colors cursor-pointer"
                  id="btn-close-upload-modal"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              {/* Form Content */}
              <form onSubmit={handleFormSubmit} className="p-8 space-y-6">
                {/* Drag and Drop Zone */}
                <div 
                  onDragEnter={handleDrag}
                  onDragOver={handleDrag}
                  onDragLeave={handleDrag}
                  onDrop={handleDrop}
                  className={`border-2 border-dashed rounded-2xl p-6 text-center cursor-pointer transition-all flex flex-col items-center justify-center ${
                    dragActive ? "border-primary bg-primary/5 scale-[0.99]" : "border-outline-variant hover:border-primary/55"
                  } ${isSpecialUploadMode ? "bg-secondary/[0.02] border-secondary/40 hover:border-secondary" : ""}`}
                  id="drag-and-drop-zone"
                >
                  <Upload className={`w-10 h-10 mb-3 animate-bounce-subtle ${isSpecialUploadMode ? "text-secondary" : "text-primary"}`} />
                  <p className="font-bold text-body-md text-on-surface">여기에 기기의 문서를 끌어서 놓으세요</p>
                  <p className="text-xs text-outline mt-1 leading-relaxed">PDF, DOCX, XLSX, TXT 형식 지원 (한국어 완전 분석 지원)</p>
                  
                  <div className="h-px bg-outline-variant/30 w-1/2 my-3"></div>
                  
                  <p className="text-[11px] text-outline font-semibold">또는 아래 예시 템플릿을 즉시 테스트해 보세요:</p>
                  <div className="flex gap-2 mt-2">
                    <button 
                      type="button"
                      onClick={() => handleLoadSample("sample1")}
                      className="px-2.5 py-1 bg-primary/5 hover:bg-primary/10 border border-primary/10 text-primary text-[10px] font-bold rounded-lg cursor-pointer animate-pulse"
                    >
                      📝 정부 바우처 결과보고서(TXT)
                    </button>
                    <button 
                      type="button"
                      onClick={() => handleLoadSample("sample2")}
                      className="px-2.5 py-1 bg-indigo-50 hover:bg-indigo-100 border border-indigo-100 text-indigo-600 text-[10px] font-bold rounded-lg cursor-pointer"
                    >
                      🏢 사내 복리후생 가이드(TXT)
                    </button>
                  </div>
                </div>

                <div className="grid grid-cols-3 gap-4">
                  {/* Document Name input */}
                  <div className="col-span-3 flex flex-col gap-1.5">
                    <label className="font-bold text-label-sm text-on-surface">문서 이름</label>
                    <input 
                      type="text"
                      required
                      value={uploadName}
                      onChange={(e) => setUploadName(e.target.value)}
                      placeholder="예시: 2024년 사업실적_최종본"
                      className="w-full bg-white border border-outline-variant rounded-xl py-2.5 px-3 text-body-sm outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all"
                    />
                  </div>
                </div>

                {/* Content input */}
                <div className="flex flex-col gap-1.5">
                  <div className="flex justify-between items-center">
                    <label className="font-bold text-label-sm text-on-surface">문서 내용 (텍스트)</label>
                    <span className="text-[10px] text-outline font-semibold">최대 20,000자</span>
                  </div>
                  <textarea 
                    rows={6}
                    value={uploadContent}
                    onChange={(e) => setUploadContent(e.target.value)}
                    placeholder="AI가 분석하고 요약할 본문 내용을 직접 붙여넣거나 위의 템플릿 샘플을 클릭하여 즉시 입력해 보세요."
                    className="w-full bg-white border border-outline-variant rounded-xl py-2.5 px-3 text-body-sm outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all font-sans"
                  ></textarea>
                </div>

                <div className="border-t border-outline-variant/30 pt-6 flex justify-end gap-3">
                  <button 
                    type="button" 
                    onClick={() => {
                      setIsUploadOpen(false);
                      setIsSpecialUploadMode(false);
                    }}
                    className="px-5 py-2.5 bg-surface-container hover:bg-surface-variant rounded-xl text-label-md text-on-surface font-semibold transition-colors cursor-pointer"
                  >
                    취소
                  </button>
                  <button 
                    type="submit"
                    disabled={isUploading}
                    className={`px-6 py-2.5 text-white text-label-md font-bold rounded-xl hover:bg-opacity-95 shadow-md transition-colors flex items-center gap-1.5 cursor-pointer disabled:opacity-50 disabled:cursor-wait ${
                      isSpecialUploadMode ? "bg-secondary shadow-secondary/10" : "bg-primary shadow-primary/10"
                    }`}
                  >
                    {isUploading ? (
                      <>
                        <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></div>
                        AI 분류 분석 중...
                      </>
                    ) : (
                      <>
                        <Sparkles className="w-4 h-4 fill-white/20 animate-pulse" />
                        {isSpecialUploadMode ? "AI 자동 정리 업로드 진행" : "AI 지능형 정렬 및 업로드"}
                      </>
                    )}
                  </button>
                </div>
              </form>
            </motion.div>
          </div>
        )}
      </AnimatePresence>
      <AnimatePresence>
        {isNewUploadOpen && (
            <div className="fixed inset-0 z-[120] flex items-center justify-center bg-black/60 backdrop-blur-sm">
              <motion.div
                  initial={{ scale: 0.95, opacity: 0 }}
                  animate={{ scale: 1, opacity: 1 }}
                  exit={{ scale: 0.95, opacity: 0 }}
                  className="bg-white rounded-3xl w-full max-w-2xl p-6 shadow-2xl border border-outline-variant relative overflow-hidden space-y-4 max-h-[85vh] flex flex-col"
              >
                <div className="flex items-center justify-between border-b border-outline-variant pb-3">
                  <div className="flex items-center gap-2 text-primary">
                    <Upload className="w-5 h-5" />
                    <h3 className="text-base font-bold">다중 파일 업로드 <span className="text-[10px] font-bold text-secondary align-middle">BETA</span></h3>
                  </div>
                  <button
                      onClick={() => setIsNewUploadOpen(false)}
                      className="p-1 hover:bg-surface-container rounded-full text-outline hover:text-on-surface cursor-pointer disabled:opacity-40"
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>

                <div
                    onDragEnter={(e) => { e.preventDefault(); setNewUploaderDragActive(true); }}
                    onDragOver={(e) => { e.preventDefault(); setNewUploaderDragActive(true); }}
                    onDragLeave={(e) => { e.preventDefault(); setNewUploaderDragActive(false); }}
                    onDrop={handleNewUploaderDrop}
                    onClick={() => newUploaderInputRef.current?.click()}
                    className={`border-2 border-dashed rounded-2xl p-6 text-center cursor-pointer transition-all flex flex-col items-center justify-center ${
                        newUploaderDragActive ? "border-primary bg-primary/5" : "border-outline-variant hover:border-primary/55"
                    }`}
                >
                  <Upload className="w-9 h-9 mb-2 text-primary" />
                  <p className="font-bold text-sm text-on-surface">파일을 여기로 끌어다 놓거나 클릭해서 선택하세요</p>
                  <p className="text-[11px] text-outline mt-1">여러 파일을 한 번에 선택할 수 있습니다 · PDF, TXT (최대 20MB)</p>
                  <input
                      ref={newUploaderInputRef}
                      type="file"
                      multiple
                      accept=".pdf,.txt"
                      onChange={handleNewUploaderPick}
                      className="hidden"
                  />
                </div>

                {uploadQueue.length > 0 && (
                    <div className="flex-1 overflow-y-auto border border-outline-variant/60 rounded-xl divide-y divide-outline-variant/40 bg-surface-container-low">
                      {uploadQueue.map((item) => (
                          <div key={item.id} className="p-2.5 flex items-center gap-3 text-xs">
                            <FileText className="w-4 h-4 text-outline shrink-0" />
                            <div className="min-w-0 flex-1">
                              <p className="font-semibold text-on-surface truncate">{item.name}</p>
                              <p className="text-[10px] text-outline">{formatBytes(item.size)}{item.error ? ` · ${item.error}` : ""}</p>
                              {item.status === "UPLOADING" && (
                                  <div className="mt-1 h-1 w-full bg-surface-container rounded-full overflow-hidden">
                                    <div className="h-full bg-primary transition-all" style={{ width: `${item.progress ?? 0}%` }}></div>
                                  </div>
                              )}
                            </div>
                            {item.status === "UPLOADING" || item.status === "PROCESSING" ? (
                                <span className="flex items-center gap-1 text-primary font-bold shrink-0">
                          <span className="w-3 h-3 border-2 border-primary border-t-transparent rounded-full animate-spin"></span>
                                  {item.status === "UPLOADING" ? `업로드 중 ${item.progress ?? 0}%` : "처리 중"}
                        </span>
                            ) : item.status === "UPLOADED" ? (
                                <span className="flex items-center gap-1 text-emerald-600 font-bold shrink-0"><Check className="w-3.5 h-3.5" /> 업로드 완료 · 처리 대기</span>
                            ) : item.status === "READY" ? (
                                <span className="flex items-center gap-1 text-emerald-600 font-bold shrink-0"><Check className="w-3.5 h-3.5" /> 완료</span>
                            ) : item.status === "FAILED" ? (
                                <span className="text-rose-500 font-bold shrink-0">실패</span>
                            ) : item.status === "INVALID" ? (
                                <span className="text-amber-600 font-bold shrink-0">제외됨</span>
                            ) : (
                                <span className="text-outline font-bold shrink-0">대기</span>
                            )}
                            {!isNewUploaderBusy && (item.status === "QUEUED" || item.status === "INVALID" || item.status === "FAILED") && (
                                <button type="button" onClick={() => removeUploadQueueItem(item.id)} className="p-1 text-outline hover:text-rose-500 shrink-0 cursor-pointer">
                                  <X className="w-3.5 h-3.5" />
                                </button>
                            )}
                            {item.status === "FAILED" && (
                                <button type="button" onClick={() => retryItem(item.id)} className="px-2 py-1 text-primary font-bold hover:underline shrink-0 cursor-pointer">
                                  재시도
                                </button>
                            )}
                          </div>
                      ))}
                    </div>
                )}

                <div className="pt-3 border-t border-outline-variant/30 flex items-center justify-between gap-2.5">
                  <p className="text-[11px] text-outline font-semibold">
                    {uploadQueue.filter((i) => i.status === "UPLOADED" || i.status === "READY").length} / {uploadQueue.filter((i) => i.status !== "INVALID").length} 완료
                  </p>
                  <div className="flex gap-2.5">
                    <button
                        type="button"
                        onClick={() => setIsNewUploadOpen(false)}
                        className="px-4 py-2 bg-surface-container hover:bg-surface-container-high rounded-xl text-xs font-bold text-on-surface cursor-pointer disabled:opacity-40"
                    >
                      닫기
                    </button>
                    <button
                        type="button"
                        onClick={runNewUploaderUpload}
                        disabled={isNewUploaderBusy || uploadQueue.every((i) => i.status !== "QUEUED")}
                        className="px-4 py-2 bg-primary text-white rounded-xl text-xs font-bold hover:bg-opacity-95 shadow-md shadow-primary/10 cursor-pointer flex items-center gap-1 disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      {isNewUploaderBusy ? "업로드 중..." : "업로드 시작"}
                    </button>
                  </div>
                </div>
              </motion.div>
            </div>
        )}
      </AnimatePresence>

      {/* 4) 새 폴더 생성 모달 */}
      <AnimatePresence>
        {isNewFolderModalOpen && (
          <div className="fixed inset-0 z-[110] flex items-center justify-center bg-black/60 backdrop-blur-sm">
            <motion.div 
              initial={{ scale: 0.95, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.95, opacity: 0 }}
              className="bg-white rounded-3xl w-full max-w-md p-6 shadow-2xl border border-outline-variant relative overflow-hidden space-y-4"
            >
              <div className="flex items-center justify-between border-b border-outline-variant pb-3">
                <div className="flex items-center gap-2 text-primary">
                  <FolderPlus className="w-5 h-5" />
                  <h3 className="text-base font-bold">새 폴더 만들기</h3>
                </div>
                <button 
                  onClick={() => {
                    setIsNewFolderModalOpen(false);
                    setNewFolderName("");
                  }}
                  className="p-1 hover:bg-surface-container rounded-full text-outline hover:text-on-surface cursor-pointer"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>

              <div className="space-y-3">
                <p className="text-xs text-outline leading-relaxed">
                  다단 구조 폴더를 만드시려면 슬래시(<span className="text-primary font-bold">/</span>)로 하위 경로를 입력하세요. <br />
                  예: <span className="text-primary font-bold">기획/디자인/2026_여름</span>
                </p>
                <div className="flex flex-col gap-1.5">
                  <label className="text-xs font-bold text-on-surface">폴더 이름</label>
                  <input 
                    type="text"
                    required
                    value={newFolderName}
                    onChange={(e) => setNewFolderName(e.target.value)}
                    placeholder="예: 프로젝트 A/결과보고서"
                    className="w-full bg-white border border-outline-variant rounded-xl py-2 px-3 text-xs outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all font-semibold"
                  />
                </div>
              </div>

              <div className="pt-3 flex justify-end gap-2.5">
                <button 
                  type="button" 
                  onClick={() => {
                    setIsNewFolderModalOpen(false);
                    setNewFolderName("");
                  }}
                  className="px-4 py-2 bg-surface-container hover:bg-surface-container-high rounded-xl text-xs font-bold text-on-surface cursor-pointer"
                >
                  취소
                </button>
                <button
                  type="button"
                  onClick={async () => {
                    const segments = newFolderName.trim().split("/").filter(Boolean);
                    if (segments.length === 0) return;
                    try {
                      const { folders: updatedFolders } = await createFolderPathViaApi(folders, segments, selectedFolder);
                      setFolders(updatedFolders);
                      setIsNewFolderModalOpen(false);
                      setNewFolderName("");
                      alert("새 폴더가 성공적으로 생성되었습니다.");
                    } catch (err) {
                      console.warn("[folders] POST /api/v1/folders 실패:", err);
                      alert("폴더 생성에 실패했습니다.");
                    }
                  }}
                  className="px-4 py-2 bg-primary text-white rounded-xl text-xs font-bold hover:bg-opacity-95 shadow-md shadow-primary/10 cursor-pointer"
                >
                  폴더 생성 완료
                </button>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>

      {/* 5) 폴더 이동 모달 */}
      <AnimatePresence>
        {isMoveModalOpen && (
          <div className="fixed inset-0 z-[110] flex items-center justify-center bg-black/60 backdrop-blur-sm">
            <motion.div 
              initial={{ scale: 0.95, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.95, opacity: 0 }}
              className="bg-white rounded-3xl w-full max-w-lg p-6 shadow-2xl border border-outline-variant relative overflow-hidden space-y-4"
            >
              <div className="flex items-center justify-between border-b border-outline-variant pb-3">
                <div className="flex items-center gap-2 text-primary">
                  <FolderInput className="w-5 h-5" />
                  <h3 className="text-base font-bold">선택한 파일({movingDocIds.length}개) 이동</h3>
                </div>
                <button 
                  onClick={() => {
                    setIsMoveModalOpen(false);
                    setIsCreatingNewFolderInMove(false);
                    setNewFolderNameInMove("");
                  }}
                  className="p-1 hover:bg-surface-container rounded-full text-outline hover:text-on-surface cursor-pointer"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>

              <div className="space-y-4">
                <div className="space-y-1.5">
                  <p className="text-xs font-bold text-on-surface">대상 폴더 선택</p>
                  <div className="border border-outline-variant/60 rounded-xl max-h-[180px] overflow-y-auto divide-y divide-outline-variant/40 bg-surface-container-low">
                    {/* Default root drive option */}
                    <div 
                      onClick={() => setMoveTargetFolder(null)}
                      className={`p-2.5 text-xs font-semibold cursor-pointer transition-colors flex items-center gap-2 ${
                        moveTargetFolder === null ? "bg-primary/5 text-primary font-bold" : "text-on-surface hover:bg-white"
                      }`}
                    >
                      <HardDrive className="w-3.5 h-3.5" /> [내 드라이브] (Root 최상위)
                    </div>
                    {/* All unique folders list */}
                    {folders.map(folder => {
                      const depth = getFolderAncestors(folder.folderId, folders).length - 1;
                      const folderName = folder.name;
                      const fullPath = getFolderPath(folder.folderId, folders);
                      return (
                        <div 
                          key={folder.folderId}
                          onClick={() => setMoveTargetFolder(folder.folderId)}
                          className={`p-2.5 text-xs font-semibold cursor-pointer transition-colors flex items-center gap-2 ${
                            moveTargetFolder === folder.folderId ? "bg-primary/5 text-primary font-bold border-l-2 border-primary" : "text-on-surface hover:bg-white"
                          }`}
                          style={{ paddingLeft: `${depth * 12 + 10}px` }}
                        >
                          <Folder className="w-3.5 h-3.5 text-outline shrink-0 animate-pulse-subtle" /> 
                          <span className="truncate">{folderName}</span>
                          {depth > 0 && (
                            <span className="text-[10px] text-outline font-normal truncate opacity-75">
                              ({fullPath})
                            </span>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>

                {/* Optional "New Folder" in move view */}
                {!isCreatingNewFolderInMove ? (
                  <button
                    type="button"
                    onClick={() => setIsCreatingNewFolderInMove(true)}
                    className="text-[11px] text-primary hover:text-secondary font-bold flex items-center gap-1 cursor-pointer"
                  >
                    + 새 폴더를 즉시 만들어 이곳으로 이동하기
                  </button>
                ) : (
                  <div className="bg-surface-container-low/50 p-3.5 rounded-xl border border-outline-variant/50 space-y-2">
                    <p className="text-[10px] text-outline font-extrabold uppercase">새 폴더 만들기</p>
                    <div className="flex gap-2">
                      <input 
                        type="text"
                        value={newFolderNameInMove}
                        onChange={(e) => setNewFolderNameInMove(e.target.value)}
                        placeholder="예: 부서공유/인사/양식"
                        className="flex-1 bg-white border border-outline-variant rounded-lg px-2.5 py-1.5 text-xs outline-none focus:ring-1 focus:ring-primary transition-all font-semibold"
                      />
                      <button
                        type="button"
                        onClick={async () => {
                          const segments = newFolderNameInMove.trim().split("/").filter(Boolean);
                          if (segments.length === 0) return;
                          try {
                            const { folders: updatedFolders, leafId } = await createFolderPathViaApi(folders, segments);
                            setFolders(updatedFolders);
                            setMoveTargetFolder(leafId);
                            setIsCreatingNewFolderInMove(false);
                            setNewFolderNameInMove("");
                          } catch (err) {
                            console.warn("[folders] POST /api/v1/folders 실패:", err);
                            alert("폴더 생성에 실패했습니다.");
                          }
                        }}
                        className="px-3 bg-secondary text-white text-xs font-bold rounded-lg hover:bg-opacity-95 cursor-pointer"
                      >
                        생성
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setIsCreatingNewFolderInMove(false);
                          setNewFolderNameInMove("");
                        }}
                        className="px-2 border border-outline-variant rounded-lg text-[10px] font-bold text-outline hover:text-on-surface cursor-pointer"
                      >
                        취소
                      </button>
                    </div>
                  </div>
                )}
              </div>

              <div className="pt-3 border-t border-outline-variant/30 flex justify-end gap-2.5">
                <button 
                  type="button" 
                  onClick={() => {
                    setIsMoveModalOpen(false);
                    setIsCreatingNewFolderInMove(false);
                    setNewFolderNameInMove("");
                  }}
                  className="px-4 py-2 bg-surface-container hover:bg-surface-container-high rounded-xl text-xs font-bold text-on-surface cursor-pointer"
                >
                  취소
                </button>
                <button 
                  type="button"
                  onClick={() => {
                    handleMoveDocuments(movingDocIds, moveTargetFolder);
                    setIsMoveModalOpen(false);
                    setIsCreatingNewFolderInMove(false);
                    setNewFolderNameInMove("");
                  }}
                  className="px-4 py-2 bg-primary text-white rounded-xl text-xs font-bold hover:bg-opacity-95 shadow-md shadow-primary/10 cursor-pointer flex items-center gap-1"
                >
                  선택한 폴더로 이동 완료
                </button>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>
      <AnimatePresence>
        {movingFolderId !== null && (
            <div className="fixed inset-0 z-[110] flex items-center justify-center bg-black/60 backdrop-blur-sm">
              <motion.div
                  initial={{ scale: 0.95, opacity: 0 }}
                  animate={{ scale: 1, opacity: 1 }}
                  exit={{ scale: 0.95, opacity: 0 }}
                  className="bg-white rounded-3xl w-full max-w-lg p-6 shadow-2xl border border-outline-variant relative overflow-hidden space-y-4"
              >
                <div className="flex items-center justify-between border-b border-outline-variant pb-3">
                  <div className="flex items-center gap-2 text-primary">
                    <FolderInput className="w-5 h-5" />
                    <h3 className="text-base font-bold">폴더 이동</h3>
                  </div>
                  <button
                      onClick={() => setMovingFolderId(null)}
                      className="p-1 hover:bg-surface-container rounded-full text-outline hover:text-on-surface cursor-pointer"
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>

                <div className="space-y-1.5">
                  <p className="text-xs font-bold text-on-surface">대상 폴더 선택</p>
                  <div className="border border-outline-variant/60 rounded-xl max-h-[220px] overflow-y-auto divide-y divide-outline-variant/40 bg-surface-container-low">
                    <div
                        onClick={() => setFolderMoveTarget(null)}
                        className={`p-2.5 text-xs font-semibold cursor-pointer transition-colors flex items-center gap-2 ${
                            folderMoveTarget === null ? "bg-primary/5 text-primary font-bold" : "text-on-surface hover:bg-white"
                        }`}
                    >
                      <HardDrive className="w-3.5 h-3.5" /> [내 드라이브] (Root 최상위)
                    </div>
                    {folders
                        .filter((folder) => !isDescendantOrSelf(folder.folderId, movingFolderId, folders))
                        .map((folder) => {
                          const depth = getFolderAncestors(folder.folderId, folders).length - 1;
                          const fullPath = getFolderPath(folder.folderId, folders);
                          return (
                              <div
                                  key={folder.folderId}
                                  onClick={() => setFolderMoveTarget(folder.folderId)}
                                  className={`p-2.5 text-xs font-semibold cursor-pointer transition-colors flex items-center gap-2 ${
                                      folderMoveTarget === folder.folderId ? "bg-primary/5 text-primary font-bold border-l-2 border-primary" : "text-on-surface hover:bg-white"
                                  }`}
                                  style={{ paddingLeft: `${depth * 12 + 10}px` }}
                              >
                                <Folder className="w-3.5 h-3.5 text-outline shrink-0" />
                                <span className="truncate">{folder.name}</span>
                                {depth > 0 && (
                                    <span className="text-[10px] text-outline font-normal truncate opacity-75">
                              ({fullPath})
                            </span>
                                )}
                              </div>
                          );
                        })}
                  </div>
                </div>

                <div className="pt-3 border-t border-outline-variant/30 flex justify-end gap-2.5">
                  <button
                      type="button"
                      onClick={() => setMovingFolderId(null)}
                      className="px-4 py-2 bg-surface-container hover:bg-surface-container-high rounded-xl text-xs font-bold text-on-surface cursor-pointer"
                  >
                    취소
                  </button>
                  <button
                      type="button"
                      onClick={() => {
                        handleMoveFolder(movingFolderId, folderMoveTarget);
                        setMovingFolderId(null);
                      }}
                      className="px-4 py-2 bg-primary text-white rounded-xl text-xs font-bold hover:bg-opacity-95 shadow-md shadow-primary/10 cursor-pointer flex items-center gap-1"
                  >
                    이동 완료
                  </button>
                </div>
              </motion.div>
            </div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}
