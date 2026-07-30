# 집사 (Jipsa)

> 문서를 올리면 내용을 분석해 정리하고, 선택한 문서를 근거로 답변하는 AI 문서 관리 서비스

집사는 PDF, DOCX, PPTX, XLSX, TXT 문서를 한곳에서 관리하는 웹 서비스입니다. 업로드한
문서의 유형·요약·태그를 생성하고, AI가 제안한 폴더 구조를 사용자가 확인한 뒤 적용할 수
있습니다. 채팅에서는 사용자가 직접 선택한 문서만 검색 범위로 사용하며, 답변과 함께 실제
근거 위치를 반환합니다.

---

## 목차

- [프로젝트 소개](#프로젝트-소개)
- [주요 기능](#주요-기능)
- [서비스 구성과 처리 흐름](#서비스-구성과-처리-흐름)
- [기술 스택](#기술-스택)
- [프로젝트 구조](#프로젝트-구조)
- [시작하기](#시작하기)
- [환경 변수](#환경-변수)
- [주요 API](#주요-api)
- [테스트와 코드 품질 검사](#테스트와-코드-품질-검사)
- [관련 문서](#관련-문서)

---

## 프로젝트 소개

문서가 쌓일수록 사용자는 파일명을 기억하거나 폴더를 직접 설계해야 하고, 필요한 정보를
찾기 위해 여러 문서를 반복해서 열어야 합니다. 집사는 이 과정을 다음 흐름으로 연결합니다.

1. 여러 형식의 문서를 업로드합니다.
2. 문서 본문과 내부 이미지를 분석하고 검색 가능한 청크로 색인합니다.
3. AI가 생성한 메타데이터와 폴더 정리 제안을 사용자가 검토합니다.
4. 선택한 문서를 대상으로 질문하고, 출처가 포함된 답변을 확인합니다.

### 지원 문서 형식

| 형식 | 텍스트·구조 추출 | 내부 이미지 OCR | 출처 위치 예시 |
|---|---:|---:|---|
| PDF | 지원 | 지원 | 페이지, 이미지 순번 |
| DOCX | 지원 | 지원 | 섹션, 문단, 표 |
| PPTX | 지원 | 지원 | 슬라이드, 도형 |
| XLSX | 지원 | 지원 | 시트, 셀 범위 |
| TXT | 지원 | 해당 없음 | 줄, 문자 범위 |

업로드 가능한 개별 파일 크기는 백엔드 설정상 최대 20MB이며, 한 요청의 최대 크기는
128MB입니다.

## 주요 기능

### 1. 문서 업로드와 파일 관리

- 여러 파일 업로드 및 처리 상태 조회
- 폴더 생성·이름 변경·이동
- 파일 이름, 문서 유형, 태그, 즐겨찾기 수정
- 파일과 폴더의 휴지통 이동·복원·영구 삭제
- 저장 공간 사용량 조회
- S3 원본 파일 보기 및 다운로드

### 2. 문서 분석과 검색 색인

- 확장자, MIME Type, Magic Byte 및 OOXML 내부 구조를 함께 검사
- 문서 형식별 텍스트와 구조 추출
- PDF 및 Office 문서 내부 이미지 추출과 EasyOCR 기반 한국어·영어 OCR
- 원본 위치를 보존한 청킹과 임베딩
- Qdrant에 사용자·파일·활성 색인 정보를 포함한 벡터 저장

### 3. AI 메타데이터와 스마트 정리

- 문서 유형, 요약, 태그 등 메타데이터 생성
- 현재 폴더 구조와 파일 정보를 바탕으로 정리안 제안
- 전체 파일 또는 방금 업로드한 파일만 대상으로 제안 가능
- 제안 결과를 미리 확인한 뒤 적용
- 사용자가 허용한 경우에만 파일 이름 변경 제안

### 4. 문서 기반 AI 채팅

- 대화 생성·조회·이름 변경·삭제
- 선택한 문서만 대상으로 하는 RAG 검색
- 단일 조회형 질문과 다문서 비교·종합 질문 처리
- 답변에 실제 인용된 출처만 반환
- 근거가 부족하면 답변 생성을 생략하고 근거 부족 상태 반환
- 답변별 사용자 피드백 저장

### 5. 인증과 운영 관리

- Google OAuth Authorization Code + PKCE 로그인
- JWT Access Token 및 Refresh Token 기반 세션
- 사용자 설정 조회·수정
- 관리자용 사용자 조회, 정지·해제, 역할 변경 및 삭제

## 서비스 구성과 처리 흐름

```text
[React + Vite]
      │ REST API
      ▼
[Spring Boot Backend] ──────── [MySQL]
      │   ├─ 인증·권한             └─ 사용자, 파일, 폴더, 대화, 작업 상태
      │   ├─ 파일·폴더 관리
      │   ├─ S3 원본 관리 ───── [Amazon S3]
      │   └─ RAG 요청 중계
      ▼
[FastAPI Local RAG]
      ├─ 문서 검증·파싱·OCR
      ├─ 구조 보존 청킹
      ├─ TEI 임베딩 ────────── [CUDA TEI]
      ├─ 벡터 검색 ─────────── [Qdrant]
      ├─ 처리 이력·청크 ────── [Local RAG DB]
      └─ 근거 기반 답변 ────── [Anthropic Claude]
```

### 업로드 처리

```text
업로드 → S3 원본 저장 → 처리 작업 생성 → RAG manifest 조회
→ 형식 검증 → 파싱·OCR → 청킹 → 임베딩 → Qdrant 색인
→ Backend callback → 파일 상태·메타데이터 갱신
```

### 질의 처리

```text
질문 + 선택 문서 ID → Backend 권한 검증 → RAG 범위 제한 검색
→ Claude 근거 기반 생성 → 인용 무결성 검증 → 답변·출처 저장
```

RAG 검색 조건에는 사용자 ID, 활성 색인 여부, 사용자가 선택한 파일 ID가 모두 포함됩니다.
참조 문서를 선택하지 않은 요청은 전체 문서 검색으로 확대되지 않습니다.

## 기술 스택

| 영역 | 기술 |
|---|---|
| Frontend | React 19, TypeScript 6, Vite 8, Tailwind CSS 4, Motion |
| 문서 미리보기 | PDF.js, Mammoth, SheetJS |
| Backend | Java 21, Spring Boot 4.1, Spring MVC, Spring Data JPA, Spring Security |
| 인증 | Google OAuth 2.0 + PKCE, JWT |
| RAG API | Python 3.12, FastAPI, Uvicorn, Pydantic |
| 문서 처리 | PyMuPDF, pypdf, python-docx, python-pptx, openpyxl |
| OCR | EasyOCR, PyTorch 2.8, OpenCV |
| 생성 모델 | Anthropic Claude |
| 임베딩·벡터 검색 | TEI, Qdrant |
| 데이터·스토리지 | MySQL 8, Local RAG DB, Amazon S3 |
| 인프라 | Docker, Docker Compose |
| 테스트·정적 분석 | JUnit, Pytest, Ruff, Mypy, ESLint |

## 프로젝트 구조

```text
INT2-Jipsa-Team04/
├── frontend/                 # React 웹 클라이언트
│   ├── src/api/              # Backend API 클라이언트
│   ├── src/components/       # 화면 및 공통 UI
│   ├── src/smart/            # 스마트 정리 상태와 흐름
│   └── src/upload/           # 업로드 상태와 UI
├── backend/                  # Spring Boot 애플리케이션 서버
│   └── src/main/java/com/jipsa/
│       ├── auth/             # OAuth, JWT, 토큰 갱신
│       ├── file/             # 파일 조회·수정·삭제
│       ├── folder/           # 폴더 트리와 휴지통
│       ├── upload/           # S3 업로드와 처리 작업 생성
│       ├── metadata/         # AI 메타데이터
│       ├── organize/         # 스마트 정리 제안·적용
│       ├── search/           # 문서 검색
│       ├── chat/             # 대화, RAG 답변, 인용, 피드백
│       └── admin/            # 관리자 기능
├── RAG/                      # FastAPI 기반 Local RAG 서비스
│   ├── src/jipsa_rag/api/    # 인제스트·검색·답변 API
│   ├── src/jipsa_rag/services/
│   ├── src/jipsa_rag/infrastructure/
│   │   ├── document/         # 형식별 파서와 이미지 처리
│   │   ├── ocr/              # OCR
│   │   ├── embedding/        # TEI 연동
│   │   ├── indexing/         # Qdrant 및 색인 저장소
│   │   └── generation/       # Claude 연동
│   └── tests/                # unit, integration, e2e, regression
├── db/init/                  # Backend DB 초기 스키마
├── embedding/                # 별도 임베딩 API 실험 구성
└── docker-compose.yml        # MySQL, Backend, Frontend 구성
```

## 시작하기

### 사전 요구사항

- Git
- Docker 및 Docker Compose
- Google OAuth Client
- Anthropic API Key
- Amazon S3 버킷과 접근 자격 증명

전체 기능을 사용하려면 Docker Compose 구성 외에 Local RAG가 사용하는 Python 3.12,
MySQL 계열 DB, Qdrant, CUDA TEI가 필요합니다. OCR의 CUDA 가속에는 호환되는 NVIDIA
환경이 필요하며, PPTX 차트·SmartArt와 XLSX 차트의 원본 Office 렌더링은 Windows의
Microsoft Office 대화형 세션을 사용합니다.

### 1. 저장소 복제

```bash
git clone https://github.com/Programmers-Intern-Program/INT2-Jipsa-Team04.git
cd INT2-Jipsa-Team04
```

### 2. 애플리케이션 환경 변수 설정

```bash
cp .env.example .env
```

`.env`의 예시 값을 실제 개발 환경 값으로 교체합니다. 비밀값이 들어 있는 `.env`는
커밋하지 않습니다.

### 3. MySQL, Backend, Frontend 실행

```bash
docker compose up --build
```

| 서비스 | 기본 주소 |
|---|---|
| Frontend | `http://localhost:5173` |
| Backend | `http://localhost:8080` |
| MySQL | `localhost:3306` |

현재 루트 `docker-compose.yml`은 MySQL, Backend, Frontend만 실행합니다. 문서 색인과
RAG 채팅을 사용하려면 다음 단계의 Local RAG를 별도로 실행해야 합니다.

### 4. Local RAG 준비 및 실행

Local RAG는 운영체제와 GPU 구성에 따라 준비 과정이 다릅니다. 먼저
[`RAG/README.md`](RAG/README.md)의 **설치와 실행** 절에 따라 Local RAG DB, Qdrant,
CUDA TEI와 환경 변수를 준비합니다.

Windows PowerShell 기준 FastAPI 실행 명령은 다음과 같습니다.

```powershell
cd RAG
uv sync --frozen
& .\scripts\start-local-rag.ps1
```

Local RAG API의 기본 주소는 `http://localhost:8077`입니다. Backend의
`RAG_BASE_URL`과 양쪽 내부 토큰을 같은 실행 환경에 맞게 설정해야 합니다.

### 개별 개발 서버 실행

Frontend:

```bash
cd frontend
npm ci
npm run dev
```

Backend:

```bash
cd backend
./gradlew bootRun
```

## 환경 변수

루트 `.env.example`이 Docker Compose로 실행하는 애플리케이션의 기준입니다.

| 변수 | 용도 |
|---|---|
| `DB_PASSWORD`, `DB_ROOT_PASSWORD` | Backend MySQL 계정 비밀번호 |
| `JWT_SECRET` | JWT 서명 키 |
| `JWT_ACCESS_EXPIRATION_MS` | Access Token 만료 시간(ms) |
| `JWT_REFRESH_EXPIRATION_MS` | Refresh Token 만료 시간(ms) |
| `ANTHROPIC_API_KEY` | Claude 호출 키 |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google OAuth Client 정보 |
| `GOOGLE_REDIRECT_URI` | Google OAuth 콜백 URI |
| `S3_BUCKET` | 원본 문서 저장 S3 버킷 |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | Backend의 S3 접근 자격 증명 |
| `AWS_REGION` | AWS 리전 |
| `JIPSA_FIELD_ENC_KEY` | 민감 필드 암호화 키 |
| `INTERNAL_TOKEN` | Backend 내부 API 인증 토큰 |
| `INTERNAL_ALLOWED_IPS` | 내부 API 접근 허용 IP |
| `RAG_BASE_URL` | Backend가 호출할 Local RAG 주소 |
| `RAG_INGEST_TOKEN` | Backend와 Local RAG 사이의 인증 토큰 |
| `APP_INGEST_ENABLED` | Backend 인제스트 작업 실행 여부 |
| `APP_STORAGE_QUOTA_BYTES` | 사용자 저장 공간 한도(byte) |

Local RAG의 전체 변수와 보안 기준은
[`RAG/.env.example`](RAG/.env.example)과
[`RAG/docs/security/environment-and-secrets.md`](RAG/docs/security/environment-and-secrets.md)를
따릅니다.

## 주요 API

### Backend 공개 API

| 영역 | 대표 경로 | 기능 |
|---|---|---|
| 인증 | `/api/v1/auth` | Google 로그인, 토큰 갱신, 로그아웃 |
| 사용자 | `/api/v1/users/me` | 내 정보와 설정 조회·수정 |
| 업로드 | `/api/v1/uploads` | 파일 업로드, 상태 및 최근 업로드 조회 |
| 파일 | `/api/v1/files` | 목록·상세·수정·휴지통·다운로드 |
| 폴더 | `/api/v1/folders` | 생성·수정·휴지통·복원·영구 삭제 |
| 메타데이터 | `/api/v1/metadata` | 지원 문서 유형 조회 |
| 스마트 정리 | `/api/v1/organize` | 현재 트리, 제안, 업로드 범위 제안, 적용 |
| 검색 | `/api/v1/search` | 문서 검색 |
| 대화 | `/api/v1/conversations` | 대화 CRUD |
| 메시지 | `/api/v1/conversations/{id}/messages` | RAG 질문·답변, 이력, 피드백 |
| 관리자 | `/api/v1/admin/users` | 사용자·제재·역할 관리 |

### Local RAG 내부 API

| Method | 경로 | 기능 |
|---|---|---|
| `POST` | `/ingest` | Backend manifest 기반 문서 색인 |
| `POST` | `/api/v1/files/process` | 전달된 manifest 직접 처리 |
| `GET` | `/api/v1/health/live` | 프로세스 생존 확인 |
| `GET` | `/api/v1/health/ready` | Local RAG DB 준비 상태 확인 |
| `POST` | `/api/v1/chunks/search` | 선택 문서의 활성 청크 검색 |
| `POST` | `/api/v1/rag/answers` | 선택 문서 기반 답변 생성 |

Local RAG의 요청·응답과 오류 계약은
[`RAG/docs/api/comprehensive-api-specification.md`](RAG/docs/api/comprehensive-api-specification.md)를
기준으로 합니다.

## 테스트와 코드 품질 검사

Frontend:

```bash
cd frontend
npm ci
npm run lint
npm run build
```

Backend:

```bash
cd backend
./gradlew test
```

Local RAG:

```powershell
cd RAG
uv sync --frozen --group dev
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest
```

실제 외부 서비스와 고정 문서를 사용하는 E2E 절차는
[`RAG/docs/testing/powershell-e2e.md`](RAG/docs/testing/powershell-e2e.md)를 확인합니다.

## 관련 문서

- [Local RAG 전체 안내](RAG/README.md)
- [Local RAG 종합 API 명세](RAG/docs/api/comprehensive-api-specification.md)
- [Backend와 Local RAG 책임 경계](RAG/docs/architecture/responsibility-boundary.md)
- [지원 형식과 OCR](RAG/docs/features/document-support-and-ocr.md)
- [Local RAG 실행 환경](RAG/docs/operations/local-runtime.md)
- [테스트 가이드](RAG/docs/testing/test-guide.md)
