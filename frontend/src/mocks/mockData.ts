// 디자인 초안(server.ts)에서 추출한 정적 mock 데이터.
// TODO: 실제 백엔드 API 연동 시 이 파일 대신 API 응답을 사용하도록 교체 (별도 이슈). 필드명은 API 문서.md 기준으로 정렬됨.
import type { Document, AISettings, Folder } from "../types";


// GET /api/v1/folders 응답 mock. Parent_Folder_IDX 기반 평면 목록.
export const mockFolders: Folder[] = [
  { folderId: 1, name: "재무 보고서", parentFolderId: null },
  { folderId: 2, name: "프로젝트 A", parentFolderId: null },
  { folderId: 3, name: "개인 문서", parentFolderId: null },
  { folderId: 4, name: "2024년", parentFolderId: 1 },
  { folderId: 5, name: "회의록", parentFolderId: 2 },
  { folderId: 6, name: "인적 사항", parentFolderId: 3 },
  { folderId: 7, name: "매출 실적", parentFolderId: 2 },
  { folderId: 8, name: "온보딩", parentFolderId: 3 }
];

export const mockDocuments: Document[] = [
  {
    id: "doc-1",
    name: "2024 하반기 경영 전략.pdf",
    content: `2024 하반기 경영 전략 및 실행 계획서.
핵심 목표:
1. 클라우드 인프라 현대화: 기존 온프레미스 레거시 시스템을 하이브리드 클라우드로 100% 전환하여 유연성과 처리 용량을 확장함. 예산 편성액: 12억 원.
2. AI 기반 워크플로우 도입: 전사 업무에 자동화 및 AI 비서를 배치하여 전체 업무 자동화율 45% 달성을 목표로 삼음. 예산 편성액: 4억 5천만 원.
3. 글로벌 시장 점유율 확대: 동남아시아 3개국(베트남, 인도네시아, 태국)에 신규 지사를 설립하고 클라우드 드라이브 현지 서비스를 전격 런칭함. 예산 편성액: 8억 원.
리스크 관리 방안: 환율 변동성 확대에 따른 자본 투자액 방어 대책 수립.`,
    sizeBytes: 2516582,
    fileType: "pdf",
    folderId: 4,
    tags: ["전략기획", "경영분석"],
    modifiedAt: "2024.05.15",
    ownerName: "홍길동 팀장",
    securityRank: "일반",
    summary: "클라우드 인프라 전환(12억), 업무 자동화 45% 달성(4.5억), 동남아 3개국 지사 설립(8억)을 골자로 하는 2024년 하반기 경영전략안입니다.",
    piiDetected: false,
    docType: "보고서",
    entities: {
      dates: ["2024년 하반기"],
      people: ["홍길동 팀장"],
      amounts: ["12억 원", "4억 5천만 원", "8억 원"],
      project: "하반기 경영 전략 및 실행 계획"
    }
  },
  {
    id: "doc-2",
    name: "클라이언트 미팅 회의록.docx",
    content: `일시: 2024년 5월 14일
참석자: 네이버 비즈니스 본부 이부장, AI Drive 개발팀 김철수 대리, 박지호 과장
주요 논의 사항:
- 클라이언트 측에서 파일 업로드 시 대용량 파일(1GB 이상)에 대한 분할 업로드 및 실시간 AI 분류 속도 개선을 요청함.
- AI 요약 결과물의 완성도 향상을 위해 3줄 요약 외에도 핵심 키워드 메타데이터를 분리하여 제공해 달라는 피드백 수집.
액션 아이템:
1. 대용량 파일 분할 처리 API 구현 검토 (AI 자동생성팀 담당, 마감: 6월 초)
2. UI/UX 설정 화면에 요약 응답 스타일 선택 옵션 추가 (박지호 과장 담당)`,
    sizeBytes: 862208,
    fileType: "docx",
    folderId: 5,
    tags: ["회의요약", "액션아이템"],
    modifiedAt: "2024.05.14",
    ownerName: "AI 자동 생성",
    securityRank: "일반",
    summary: "대용량 업로드 개선 및 AI 메타데이터 추가를 요청한 클라이언트 미팅 결과를 담고 있으며, 분할 업로드 및 UI 개선이 액션 아이템으로 도출되었습니다.",
    piiDetected: false,
    docType: "회의록",
    entities: {
      dates: ["2024년 5월 14일", "6월 초"],
      people: ["이부장", "김철수 대리", "박지호 과장"],
      amounts: ["1GB 이상"],
      project: "대용량 파일 분할 처리 API 및 UI 개선 프로젝트"
    }
  },
  {
    id: "doc-3",
    name: "5월 예산 집행 현황.xlsx",
    content: `5월 예산 집행 세부 데이터 (단위: 원)
- 클라우드 인프라 현대화 부문:
  * 서버 아키텍처 라이선스 및 컨설팅: 1,200,000,000 집행 완료. (사업계획 대비 100% 일치)
- AI 기반 워크플로우 도입 부문:
  * 대규모 언어 모델 API 이용료: 150,000,000 집행.
  * 지능형 자동화 컨설팅: 300,000,000 집행. (총 4억 5천만 원 집행 완료, 예산 범위 부합)
- 글로벌 시장 점유율 확대 부문:
  * 베트남/태국/인도네시아 마케팅 및 현지 채용: 680,000,000 집행. (예산 8억 대비 약 1억 2천만 원 부족 상태로 긴급 추가 편성이 요구됨. 현재 마케팅 예산이 약 15% 부족함.)`,
    sizeBytes: 1153434,
    fileType: "xlsx",
    folderId: 4,
    tags: ["재무데이터", "검토필요"],
    modifiedAt: "2024.05.12",
    ownerName: "김철수 대리",
    securityRank: "일반",
    summary: "클라우드 12억, AI 자동화 4.5억은 사업 계획대로 정상 집행되었으나, 글로벌 마케팅 부문은 예산 대비 약 15%(1억 2천만 원) 부족한 상태임을 보고합니다.",
    piiDetected: false,
    docType: "보고서",
    entities: {
      dates: ["2024년 5월"],
      people: ["김철수 대리"],
      amounts: ["12억 원", "1억 5천만 원", "3억 원", "4억 5천만 원", "6억 8천만 원", "8억 원", "1억 2천만 원"],
      project: "5월 예산 집행 현황 분석"
    }
  },
  {
    id: "doc-4",
    name: "2025 비밀 유지 계약서_v2.pdf",
    content: `비밀유지계약서 (NDA)
제 1 조 (목적) 본 계약은 AI Drive 개발사 (주)넥서스인텔리전스(이하 "갑")와 공동 연구 파트너사인 (주)미래소프트(이하 "을") 간에 제공되는 모든 기술적, 상업적 정보에 대한 기밀 유지를 목적으로 한다.
제 2 조 (기밀 정보의 범위) "을"은 프로젝트와 관련하여 취득한 일체의 기술 사양, 알고리즘 소스 코드, 및 고객 개인정보를 외부로 유출하여서는 안 된다.
제 3 조 (손해 배상) 위반 시 일체 법적 책임 및 손해배상액으로 금 일억 원(100,000,000원)을 지급하기로 합의함.
개인 인적 사항 기재란:
갑 대표자 이순신 (전화번호: 010-1234-5678, 이메일: ssin@nexus.ai)
을 대표자 홍길동 (전화번호: 010-9876-5432, 이메일: kildong@miraesoft.com)`,
    sizeBytes: 6081741,
    fileType: "pdf",
    folderId: 6,
    tags: ["법무", "기밀"],
    modifiedAt: "2025.01.10",
    ownerName: "김지능",
    securityRank: "기밀",
    summary: "(주)넥서스인텔리전스와 (주)미래소프트 간의 NDA 계약 문서로, 소스코드 및 개인정보 보호 의무와 위반 시 1억 원의 손해배상 조항이 기술되어 있으며 대표자 연락처를 포함합니다.",
    piiDetected: true,
    docType: "계약서",
    entities: {
      dates: ["2025년 1월 10일"],
      people: ["이순신", "홍길동"],
      amounts: ["1억 원"],
      project: "AI Drive 공동 연구 개발 NDA"
    }
  },
  {
    id: "doc-5",
    name: "연간 매출 실적 분석.pdf",
    content: `연간 매출 및 성장률 추이 분석.
2023년 총 매출액: 450억 원 (전년 대비 18% 성장)
2024년 총 매출액: 530억 원 (전년 대비 17.7% 성장)
주요 매출 견인 요인: 기업향 SaaS 클라우드 드라이브 'AI Drive' 라이선스 계약 증가(매출의 60% 비중 차지).
AI 분석 의견: 엔터프라이즈 전용 지능형 문서 분류기 및 RAG 검색 솔루션 추가 도입 이후 대형 기업 고객 리텐션율이 95% 이상으로 대폭 향상됨.`,
    sizeBytes: 13002342,
    fileType: "pdf",
    folderId: 7,
    tags: ["재무", "AI추천"],
    modifiedAt: "2024.12.28",
    ownerName: "김지능",
    securityRank: "일반",
    summary: "2023년 및 2024년 매출 성장률 추이를 다루고 있으며, 엔터프라이즈 타겟 'AI Drive' SaaS 도입 효과로 530억 매출 돌파 및 리텐션율 95% 달성을 요약합니다.",
    piiDetected: false,
    docType: "보고서",
    entities: {
      dates: ["2023년", "2024년"],
      people: ["김지능"],
      amounts: ["450억 원", "530억 원"],
      project: "연간 매출 실적 분석 프로젝트"
    }
  },
  {
    id: "doc-6",
    name: "신규 입사자 온보딩 가이드.docx",
    content: `Welcome to AI Drive! 신규 입사자 온보딩 매뉴얼.
우리는 세상에서 가장 아름답고 지능적인 문서 관리 공간을 혁신합니다.
근무 가이드:
1. 출퇴근 시간: 오전 10시 ~ 오후 7시 (시차출퇴근제 가능)
2. 사내 도서 및 교육비 무제한 지원
3. 협업 도구: Slack, Notion, Jira, 그리고 사내 특화 솔루션인 AI Drive
AI 기능 100% 활용하기:
- 'AI 채팅 및 검색' 서비스를 열어 사내 위키나 가이드를 바로 질문해 보세요. AI가 실시간으로 문서를 뒤져 정확히 답해 줍니다.`,
    sizeBytes: 3355443,
    fileType: "docx",
    folderId: 8,
    tags: ["인사"],
    modifiedAt: "2024.11.05",
    ownerName: "김지능",
    securityRank: "일반",
    summary: "신규 입사자를 위한 근무 가이드, 복리후생 정보 및 사내 핵심 솔루션인 AI Drive의 AI 기반 RAG 검색 활용 가이드를 간결하게 안내합니다.",
    piiDetected: false,
    docType: "가이드라인",
    entities: {
      dates: ["2024년 11월 05일"],
      people: ["김지능"],
      amounts: ["무제한 지원"],
      project: "신규 입사자 온보딩 TF"
    }
  }
];

export const mockAISettings: AISettings = {
  sensitivity: 0.85,
  voiceModel: "Nova (명확하고 신뢰감 있는)",
  responseStyle: "간결형",
  instantSummary: true,
  autoHighlight: false,
  pushNotification: true
};

