/* ============================================================================
   Jipsa_AWS_Server_DB_v1.sql
   ---------------------------------------------------------------------------
   Project : Jipsa(집사) - 문서 정리 비서 / AWS Server DB
   Purpose : AWS 서버 MariaDB 초기 생성용 DDL
             - 사용자 인증/설정, OAuth, Refresh Token, 제재 이력 관리
             - 파일 등록, 폴더, 업로드 배치, 작업 상태, 메타데이터 관리
             - 대화방, 메시지, 서버 검색용 Chunk, 메시지 인용 로그 관리
   Target  : MariaDB 10.6+ 기준 / MySQL 8.0+ 호환 지향
   Charset : utf8mb4 / utf8mb4_unicode_ci

   핵심 설계 원칙
     1) AWS Server DB는 사용자, 파일, 채팅, 작업 상태, 서버 검색/응답용 데이터를 관리한다.
     2) AWS Chunk는 서버 검색/응답용 청크이며,
        Local RAG_Chunk가 실제 RAG 파이프라인의 원본 청크(Source of Truth)이다.
     3) Local RAG DB 및 VectorDB와는 물리 FK를 연결하지 않는다.
        Local RAG DB의 File_IDX, Users_IDX, Folder_IDX, Server_Job_IDX는 이 DB의 식별자를 복사한 외부 참조값이다.
     4) 파일 삭제, 폴더 이동, 파일명 변경, 재파싱, 재색인은
        별도 동기화 백엔드가 AWS DB, Local RAG DB, VectorDB에 반영한다.
     5) Job은 사용자에게 노출되는 대표 작업 상태를 관리하고,
        Local RAG_Index_Run은 Local RAG Service 내부 실행 이력을 별도로 관리한다.
     6) 검정색 AWS 테이블은 팀 회의 후 최종 컬럼명/상태값/제약조건을 조정할 수 있다.
   ============================================================================ */

CREATE DATABASE IF NOT EXISTS `Jipsa_AWS_Server`
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;

USE `Jipsa_AWS_Server`;

SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci;
SET default_storage_engine = InnoDB;
SET FOREIGN_KEY_CHECKS = 0;

/*
   신규 AWS Server DB 초기 생성용 파일이다.
   Local RAG DB 및 VectorDB는 이 파일의 생성 대상이 아니다.
   이 DB 내부 테이블끼리만 FK를 연결하며, Local RAG DB/VectorDB와는 물리 FK를 연결하지 않는다.
*/

/* =========================
   1. Users
   설명:
     - Jipsa 계정의 권한, 잠금, 상태를 관리하는 최상위 사용자 테이블이다.
     - OAuth 기반 로그인을 전제로 OAuth_Connections에서 제공자 계정 정보를 관리한다.
   ========================= */
CREATE TABLE `Users` (
    `Users_IDX` BIGINT NOT NULL AUTO_INCREMENT COMMENT '사용자 고유 식별자(PK)',
    `Locked_Until` DATETIME(6) NULL COMMENT '계정 잠금 해제 예정 일시',
    `Locked_Reason` TEXT NULL COMMENT '계정 잠금 사유. 개인정보 포함 금지',
    `Role` VARCHAR(30) NOT NULL DEFAULT 'USERS' COMMENT '사용자 권한: ADMIN 또는 USERS',
    `Status` VARCHAR(30) NOT NULL DEFAULT 'ACTIVE' COMMENT '사용자 상태',
    `Created_At` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '생성 일시',
    `Updated_At` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '수정 일시',
    `Del` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '삭제 여부. 0=정상, 1=삭제',

    PRIMARY KEY (`Users_IDX`),

    KEY `IX_Users_Role_Del` (`Role`, `Del`),
    KEY `IX_Users_Status_Del` (`Status`, `Del`),
    KEY `IX_Users_LockedUntil` (`Locked_Until`),

    CONSTRAINT `CK_Users_Role`
        CHECK (`Role` IN ('ADMIN', 'USERS')),
    CONSTRAINT `CK_Users_Status`
        CHECK (`Status` IN ('ACTIVE', 'LOCKED', 'SUSPENDED', 'WITHDRAWN')),
    CONSTRAINT `CK_Users_Del`
        CHECK (`Del` IN (0, 1))
) COMMENT '사용자 계정 권한, 잠금, 상태 관리 테이블';

/* =========================
   2. Users_Information
   설명:
     - 사용자 표시명과 프로필 이미지 등 서비스 프로필 정보를 저장한다.
     - OAuth 제공자 원본 개인정보는 OAuth_Connections에 저장하지 않고 필요한 최소 정보만 서비스 계층에서 사용한다.
   주의:
     - 개인정보는 암호화하여 저장해야 한다.
   ========================= */
CREATE TABLE `Users_Information` (
    `Users_Information_IDX` BIGINT NOT NULL AUTO_INCREMENT COMMENT '사용자 정보 고유 식별자(PK)',
    `Users_IDX` BIGINT NOT NULL COMMENT '사용자 식별자(FK)',
    `Name_Enc` TEXT NOT NULL COMMENT '암호화된 사용자 이름 또는 표시명',
    `Profile_Image_URL` VARCHAR(1024) NULL COMMENT '프로필 이미지 URL. 접근 토큰 포함 금지',
    `Created_At` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '생성 일시',
    `Updated_At` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '수정 일시',
    `Del` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '삭제 여부. 0=정상, 1=삭제',

    PRIMARY KEY (`Users_Information_IDX`),

    UNIQUE KEY `UK_UsersInformation_Users` (`Users_IDX`),
    KEY `IX_UsersInformation_Del` (`Del`),

    CONSTRAINT `FK_UsersInformation_Users`
        FOREIGN KEY (`Users_IDX`)
        REFERENCES `Users` (`Users_IDX`),
    CONSTRAINT `CK_UsersInformation_Del`
        CHECK (`Del` IN (0, 1))
) COMMENT '사용자 프로필 및 표시 정보 테이블';

/* =========================
   3. User_Setting
   설명:
     - 사용자별 자동 분류 민감도, 음성 모드, 응답 스타일, 알림 설정을 관리한다.
   ========================= */
CREATE TABLE `User_Setting` (
    `Users_IDX` BIGINT NOT NULL COMMENT '사용자 식별자(PK/FK)',
    `Auto_Classification_Sensitivity` DECIMAL(4, 3) NOT NULL DEFAULT 0.500 COMMENT '자동 분류 민감도. 0.000~1.000',
    `Voice_Mode` VARCHAR(20) NOT NULL DEFAULT 'OFF' COMMENT '음성 모드 설정',
    `Response_Style` VARCHAR(20) NOT NULL DEFAULT 'BALANCED' COMMENT 'AI 응답 스타일',
    `Instant_Summary` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '즉시 요약 사용 여부',
    `Auto_Highlight` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '자동 하이라이트 사용 여부',
    `Push_Notification` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '푸시 알림 사용 여부',
    `Created_At` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '생성 일시',
    `Updated_At` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '수정 일시',

    PRIMARY KEY (`Users_IDX`),

    CONSTRAINT `FK_UserSetting_Users`
        FOREIGN KEY (`Users_IDX`)
        REFERENCES `Users` (`Users_IDX`),
    CONSTRAINT `CK_UserSetting_Sensitivity`
        CHECK (`Auto_Classification_Sensitivity` >= 0 AND `Auto_Classification_Sensitivity` <= 1),
    CONSTRAINT `CK_UserSetting_Boolean`
        CHECK (`Instant_Summary` IN (0, 1) AND `Auto_Highlight` IN (0, 1) AND `Push_Notification` IN (0, 1))
) COMMENT '사용자별 서비스 설정 테이블';

/* =========================
   4. Refresh_Tokens
   설명:
     - 로그인 세션 유지를 위한 Refresh Token 해시와 만료/폐기 상태를 저장한다.
     - 원문 Refresh Token은 저장하지 않고 Token_Hash만 저장한다.
   ========================= */
CREATE TABLE `Refresh_Tokens` (
    `Refresh_Tokens_IDX` BIGINT NOT NULL AUTO_INCREMENT COMMENT '리프레시 토큰 고유 식별자(PK)',
    `Users_IDX` BIGINT NOT NULL COMMENT '사용자 식별자(FK)',
    `Expires_At` DATETIME(6) NOT NULL COMMENT '토큰 만료 일시',
    `Token_Hash` VARCHAR(255) NOT NULL COMMENT '리프레시 토큰 해시값. 원문 토큰 저장 금지',
    `Last_Used_At` DATETIME(6) NULL COMMENT '마지막 사용 일시',
    `Revoked_Reason` VARCHAR(255) NULL COMMENT '토큰 폐기 사유',
    `Revoked_At` DATETIME(6) NULL COMMENT '토큰 폐기 일시',
    `Created_At` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '생성 일시',
    `Updated_At` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '수정 일시',

    PRIMARY KEY (`Refresh_Tokens_IDX`),

    UNIQUE KEY `UK_RefreshTokens_TokenHash` (`Token_Hash`),
    KEY `IX_RefreshTokens_Users_Revoked_Expires` (`Users_IDX`, `Revoked_At`, `Expires_At`),
    KEY `IX_RefreshTokens_Expires` (`Expires_At`),

    CONSTRAINT `FK_RefreshTokens_Users`
        FOREIGN KEY (`Users_IDX`)
        REFERENCES `Users` (`Users_IDX`)
) COMMENT '리프레시 토큰 해시 및 세션 폐기 상태 관리 테이블';

/* =========================
   5. OAuth_Connections
   설명:
     - OAuth 제공자 계정과 서비스 사용자 계정의 연결 정보를 관리한다.
     - 같은 제공자에서 같은 Provider_User_ID는 한 사용자에게만 연결되어야 한다.
   ========================= */
CREATE TABLE `OAuth_Connections` (
    `OAuth_Connections_IDX` BIGINT NOT NULL AUTO_INCREMENT COMMENT 'OAuth 연결 고유 식별자(PK)',
    `Users_IDX` BIGINT NOT NULL COMMENT '사용자 식별자(FK)',
    `OAuth_Provider` VARCHAR(20) NOT NULL COMMENT 'OAuth 제공자. GOOGLE, KAKAO, NAVER 등',
    `Provider_User_ID` VARCHAR(255) NOT NULL COMMENT 'OAuth 제공자 사용자 고유 ID',
    `Created_At` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '생성 일시',
    `Updated_At` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '수정 일시',
    `Del` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '삭제 여부. 0=정상, 1=삭제',

    PRIMARY KEY (`OAuth_Connections_IDX`),

    UNIQUE KEY `UK_OAuth_Provider_ProviderUserID_Del` (`OAuth_Provider`, `Provider_User_ID`, `Del`),
    UNIQUE KEY `UK_OAuth_Provider_Users_Del` (`OAuth_Provider`, `Users_IDX`, `Del`),
    KEY `IX_OAuthConnections_Users` (`Users_IDX`, `Del`),

    CONSTRAINT `FK_OAuthConnections_Users`
        FOREIGN KEY (`Users_IDX`)
        REFERENCES `Users` (`Users_IDX`),
    CONSTRAINT `CK_OAuthConnections_Provider`
        CHECK (`OAuth_Provider` IN ('GOOGLE', 'KAKAO', 'NAVER', 'APPLE')),
    CONSTRAINT `CK_OAuthConnections_Del`
        CHECK (`Del` IN (0, 1))
) COMMENT 'OAuth 제공자 계정 연결 정보 테이블';

/* =========================
   6. User_Sanctions
   설명:
     - 사용자 제재 이력과 해제 이력을 저장한다.
     - 제재 당시 사용자 상태와 해제 시 복원할 상태를 함께 관리한다.
   ========================= */
CREATE TABLE `User_Sanctions` (
    `User_Sanction_IDX` BIGINT NOT NULL AUTO_INCREMENT COMMENT '사용자 제재 고유 식별자(PK)',
    `Users_IDX` BIGINT NOT NULL COMMENT '제재 대상 사용자 식별자(FK)',
    `Sanctioned_By_Users_IDX` BIGINT NOT NULL COMMENT '제재 처리 관리자 사용자 식별자(FK)',
    `Lifted_By_Users_IDX` BIGINT NULL COMMENT '제재 해제 관리자 사용자 식별자(FK)',
    `Sanction_Type` VARCHAR(30) NOT NULL COMMENT '제재 유형',
    `Sanction_Status` VARCHAR(30) NOT NULL DEFAULT 'ACTIVE' COMMENT '제재 상태',
    `Reason` TEXT NOT NULL COMMENT '제재 사유. 민감정보 포함 금지',
    `Restore_User_Status` VARCHAR(30) NULL COMMENT '제재 해제 후 복원할 사용자 상태',
    `Started_At` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '제재 시작 일시',
    `Expires_At` DATETIME(6) NULL COMMENT '제재 만료 예정 일시',
    `Lifted_At` DATETIME(6) NULL COMMENT '제재 해제 일시',
    `Lift_Reason` TEXT NULL COMMENT '제재 해제 사유',

    PRIMARY KEY (`User_Sanction_IDX`),

    KEY `IX_UserSanctions_Target_Status_Expires` (`Users_IDX`, `Sanction_Status`, `Expires_At`),
    KEY `IX_UserSanctions_Created` (`Started_At`),
    KEY `IX_UserSanctions_LiftedBy` (`Lifted_By_Users_IDX`, `Lifted_At`),

    CONSTRAINT `FK_UserSanctions_TargetUsers`
        FOREIGN KEY (`Users_IDX`)
        REFERENCES `Users` (`Users_IDX`),
    CONSTRAINT `FK_UserSanctions_SanctionedByUsers`
        FOREIGN KEY (`Sanctioned_By_Users_IDX`)
        REFERENCES `Users` (`Users_IDX`),
    CONSTRAINT `FK_UserSanctions_LiftedByUsers`
        FOREIGN KEY (`Lifted_By_Users_IDX`)
        REFERENCES `Users` (`Users_IDX`),
    CONSTRAINT `CK_UserSanctions_Type`
        CHECK (`Sanction_Type` IN ('WARNING', 'TEMP_SUSPEND', 'PERMANENT_SUSPEND', 'UPLOAD_LIMIT', 'LOGIN_BLOCK', 'ACCOUNT_DELETE')),
    CONSTRAINT `CK_UserSanctions_Status`
        CHECK (`Sanction_Status` IN ('ACTIVE', 'EXPIRED', 'LIFTED', 'CANCELLED')),
    CONSTRAINT `CK_UserSanctions_TimeRange`
        CHECK (`Expires_At` IS NULL OR `Expires_At` >= `Started_At`)
) COMMENT '사용자 제재 및 해제 이력 테이블';

/* =========================
   7. Folder
   설명:
     - 사용자별 문서 폴더 계층을 관리한다.
   ========================= */
CREATE TABLE `Folder` (
    `Folder_IDX` BIGINT NOT NULL AUTO_INCREMENT COMMENT '폴더 고유 식별자(PK)',
    `Users_IDX` BIGINT NOT NULL COMMENT '사용자 식별자(FK)',
    `Parent_Folder_IDX` BIGINT NULL COMMENT '상위 폴더 식별자(Self FK). NULL이면 루트 폴더',
    `Name` VARCHAR(255) NOT NULL COMMENT '폴더명',
    `Created_At` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '생성 일시',
    `Updated_At` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '수정 일시',
    `Deleted_At` DATETIME(6) NULL COMMENT '논리 삭제 일시. NULL이면 활성 상태',

    PRIMARY KEY (`Folder_IDX`),

    KEY `IX_Folder_Users_Parent` (`Users_IDX`, `Parent_Folder_IDX`, `Deleted_At`),
    KEY `IX_Folder_Parent` (`Parent_Folder_IDX`),

    CONSTRAINT `FK_Folder_Users`
        FOREIGN KEY (`Users_IDX`)
        REFERENCES `Users` (`Users_IDX`),
    CONSTRAINT `FK_Folder_ParentFolder`
        FOREIGN KEY (`Parent_Folder_IDX`)
        REFERENCES `Folder` (`Folder_IDX`)
) COMMENT '사용자 문서 폴더 계층 테이블';

/* =========================
   8. Uploads
   주의:
     - 설명이 정확하지 않을 수 있음.
   설명:
     - 사용자의 업로드 배치 단위 상태를 관리한다.
   ========================= */
CREATE TABLE `Uploads` (
    `Uploads_IDX` BIGINT NOT NULL AUTO_INCREMENT COMMENT '업로드 배치 고유 식별자(PK)',
    `Users_IDX` BIGINT NOT NULL COMMENT '사용자 식별자(FK)',
    `Status` VARCHAR(30) NOT NULL DEFAULT 'PENDING' COMMENT '업로드 배치 상태',
    `Total` INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '업로드 대상 파일 총 개수',
    `Created_At` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '생성 일시',
    `Finished_At` DATETIME(6) NULL COMMENT '업로드 배치 종료 일시',
    `Idempotency_Key` VARCHAR(255) NULL COMMENT '업로드 재시도 멱등성 키. 동일 사용자 내 중복 방지',

    PRIMARY KEY (`Uploads_IDX`),

    KEY `IX_Uploads_Users_Status` (`Users_IDX`, `Status`, `Created_At`),
    UNIQUE KEY `UK_Uploads_User_IdempotencyKey` (`Users_IDX`, `Idempotency_Key`),

    CONSTRAINT `FK_Uploads_Users`
        FOREIGN KEY (`Users_IDX`)
        REFERENCES `Users` (`Users_IDX`),
    CONSTRAINT `CK_Uploads_Status`
        CHECK (`Status` IN ('PENDING', 'UPLOADING', 'COMPLETED', 'FAILED', 'CANCELLED')),
    CONSTRAINT `CK_Uploads_Total`
        CHECK (`Total` >= 0)
) COMMENT '파일 업로드 배치 상태 테이블';

/* =========================
   9. File
   설명:
     - AWS S3에 업로드된 파일의 서버 메타데이터와 처리 상태를 저장한다.
     - 실제 파일 본문은 S3에 저장하고 이 테이블은 참조 정보와 상태만 관리한다.
   ========================= */
CREATE TABLE `File` (
    `File_IDX` BIGINT NOT NULL AUTO_INCREMENT COMMENT '파일 고유 식별자(PK)',
    `Users_IDX` BIGINT NOT NULL COMMENT '사용자 식별자(FK)',
    `Folder_IDX` BIGINT NULL COMMENT '폴더 식별자(FK)',
    `Uploads_IDX` BIGINT NULL COMMENT '업로드 배치 식별자(FK)',
    `Name` VARCHAR(255) NOT NULL COMMENT '파일명',
    `S3_Key` VARCHAR(512) NOT NULL COMMENT 'S3 객체 키',
    `File_Type` VARCHAR(50) NOT NULL COMMENT '파일 타입 또는 확장자',
    `Size_Bytes` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '파일 크기(byte)',
    `Status` VARCHAR(30) NOT NULL DEFAULT 'UPLOADED' COMMENT '파일 처리 상태',
    `Security_Rank` VARCHAR(30) NULL COMMENT '보안 등급 또는 민감도 등급',
    `PII_Detected` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '개인정보 탐지 여부',
    `Error_Message` TEXT NULL COMMENT '파일 처리 오류 메시지. 원문/토큰/민감정보 저장 금지',
    `Owner_Message` VARCHAR(255) NULL COMMENT '소유자 표시 메시지',
    `Owner_Name` VARCHAR(255) NULL COMMENT '소유자 표시 이름',
    `Star` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '즐겨찾기 여부',
    `Processing_Stage` VARCHAR(50) NULL COMMENT '현재 처리 단계',
    `Created_At` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '생성 일시',
    `Updated_At` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '수정 일시',
    `Deleted_At` DATETIME(6) NULL COMMENT '논리 삭제 일시. NULL이면 활성 상태',

    PRIMARY KEY (`File_IDX`),

    UNIQUE KEY `UK_File_S3Key` (`S3_Key`),
    KEY `IX_File_Users_Folder` (`Users_IDX`, `Folder_IDX`, `Deleted_At`),
    KEY `IX_File_Uploads` (`Uploads_IDX`),
    KEY `IX_File_Status_Stage` (`Status`, `Processing_Stage`, `Updated_At`),
    KEY `IX_File_Type` (`File_Type`),

    CONSTRAINT `FK_File_Users`
        FOREIGN KEY (`Users_IDX`)
        REFERENCES `Users` (`Users_IDX`),
    CONSTRAINT `FK_File_Folder`
        FOREIGN KEY (`Folder_IDX`)
        REFERENCES `Folder` (`Folder_IDX`),
    CONSTRAINT `FK_File_Uploads`
        FOREIGN KEY (`Uploads_IDX`)
        REFERENCES `Uploads` (`Uploads_IDX`),
    CONSTRAINT `CK_File_SizeBytes`
        CHECK (`Size_Bytes` >= 0),
    CONSTRAINT `CK_File_Status`
        CHECK (`Status` IN ('UPLOADED', 'PROCESSING', 'READY', 'FAILED', 'DELETED')),
    CONSTRAINT `CK_File_Boolean`
        CHECK (`PII_Detected` IN (0, 1) AND `Star` IN (0, 1))
) COMMENT 'S3 파일 등록 정보 및 서버 처리 상태 테이블';

/* =========================
   10. File_Metadata
   설명:
     - 파일의 AI 요약, 태그, 키워드 등 파생 메타데이터를 저장한다.
   ========================= */
CREATE TABLE `File_Metadata` (
    `File_IDX` BIGINT NOT NULL COMMENT '파일 식별자(PK/FK)',
    `File_Type` VARCHAR(50) NOT NULL COMMENT '파일 타입 스냅샷',
    `Summary` TEXT NULL COMMENT '파일 요약 결과',
    `Tags` JSON NULL COMMENT '태그 JSON 배열',
    `Keywords` JSON NULL COMMENT '키워드 JSON 배열',
    `Document_Type` VARCHAR(100) NULL COMMENT '문서 종류(사용자 선택 분류). NULL이면 미분류',
    `Extraction_Status` VARCHAR(30) NULL COMMENT 'AI 메타데이터 추출 상태. NULL이면 미실행',
    `Extraction_Confidence` DECIMAL(4,3) NULL COMMENT '추출 신뢰도 0.000~1.000',
    `Extracted_Entities` JSON NULL COMMENT '추출된 일반 엔티티(dates/people/amounts/org 등)',
    `Extraction_Index_Version` INT UNSIGNED NULL COMMENT 'AI 메타데이터가 생성된 색인 버전. 오래된 콜백 무시용',
    `Created_At` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '생성 일시',
    `Updated_At` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '수정 일시',

    PRIMARY KEY (`File_IDX`),

    CONSTRAINT `FK_FileMetadata_File`
        FOREIGN KEY (`File_IDX`)
        REFERENCES `File` (`File_IDX`)
) COMMENT '파일 요약, 태그, 키워드 등 파생 메타데이터 테이블';

/* =========================
   11. Job
   설명:
     - 파일 업로드 후 파싱, 청킹, 색인, 요약 등 비동기 작업의 대표 상태를 관리한다.
     - 상세 RAG 실행 이력은 Local RAG DB의 RAG_Index_Run에서 관리한다.
   ========================= */
CREATE TABLE `Job` (
    `Job_IDX` BIGINT NOT NULL AUTO_INCREMENT COMMENT '작업 고유 식별자(PK)',
    `File_IDX` BIGINT NULL COMMENT '작업 대상 파일 식별자(FK)',
    `Uploads_IDX` BIGINT NULL COMMENT '업로드 배치 식별자(FK)',
    `Job_Type` VARCHAR(50) NOT NULL COMMENT '작업 유형',
    `Job_Status` VARCHAR(50) NOT NULL DEFAULT 'PENDING' COMMENT '작업 상태',
    `Priority` INT NOT NULL DEFAULT 0 COMMENT '작업 우선순위. 값이 클수록 우선',
    `Attempts` INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '현재 시도 횟수',
    `Max_Attempts` INT UNSIGNED NOT NULL DEFAULT 3 COMMENT '최대 시도 횟수',
    `Error_Message` TEXT NULL COMMENT '작업 오류 메시지. 원문/토큰/민감정보 저장 금지',
    `Next_Attempt_At` DATETIME(6) NULL COMMENT '다음 재시도 예정 일시',
    `Worker_ID` VARCHAR(64) NULL COMMENT '작업을 점유한 워커 ID',
    `Ownership_Expires_At` DATETIME(6) NULL COMMENT '워커 점유 만료 일시',
    `Created_At` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '생성 일시',
    `Started_At` DATETIME(6) NULL COMMENT '작업 시작 일시',
    `Finished_At` DATETIME(6) NULL COMMENT '작업 종료 일시',

    PRIMARY KEY (`Job_IDX`),

    KEY `IX_Job_Status_Priority_Created` (`Job_Status`, `Priority`, `Created_At`),
    KEY `IX_Job_File` (`File_IDX`, `Created_At`),
    KEY `IX_Job_Uploads` (`Uploads_IDX`, `Created_At`),
    KEY `IX_Job_Worker_Ownership` (`Worker_ID`, `Ownership_Expires_At`),
    KEY `IX_Job_NextAttempt` (`Next_Attempt_At`),

    CONSTRAINT `FK_Job_File`
        FOREIGN KEY (`File_IDX`)
        REFERENCES `File` (`File_IDX`),
    CONSTRAINT `FK_Job_Uploads`
        FOREIGN KEY (`Uploads_IDX`)
        REFERENCES `Uploads` (`Uploads_IDX`),
    CONSTRAINT `CK_Job_Status`
        CHECK (`Job_Status` IN ('PENDING', 'RUNNING', 'SUCCESS', 'FAILED', 'CANCELLED', 'RETRY_WAIT')),
    CONSTRAINT `CK_Job_Attempts`
        CHECK (`Attempts` <= `Max_Attempts`),
    CONSTRAINT `CK_Job_TimeRange`
        CHECK (`Started_At` IS NULL OR `Finished_At` IS NULL OR `Finished_At` >= `Started_At`)
) COMMENT '비동기 파일 처리 작업 대표 상태 테이블';

/* =========================
   12. Chunk
   설명:
     - AWS 서버 DB에서 검색/응답을 빠르게 처리하기 위한 서버 검색용 청크 테이블이다.
     - 실제 RAG 원본 청크의 Source of Truth는 Local RAG DB의 RAG_Chunk이다.
   ========================= */
CREATE TABLE `Chunk` (
    `Chunk_IDX` BIGINT NOT NULL AUTO_INCREMENT COMMENT '서버 검색용 청크 고유 식별자(PK)',
    `Chunk_ID` VARCHAR(128) NOT NULL COMMENT '청크 고유 식별자. VectorDB RAG_Chunk_Vector.Chunk_ID와 논리 매핑',
    `File_IDX` BIGINT NOT NULL COMMENT '파일 식별자(FK)',
    `Chunk_Index` INT UNSIGNED NOT NULL COMMENT '파일 내 청크 순번',
    `Content` TEXT NOT NULL COMMENT '서버 검색/응답용 청크 내용 스냅샷',
    `Page` INT UNSIGNED NULL COMMENT '페이지 번호',
    `Index_Version` INT UNSIGNED NOT NULL DEFAULT 1 COMMENT '청크가 생성된 색인 버전',
    `Created_At` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '생성 일시',

    PRIMARY KEY (`Chunk_IDX`),

    UNIQUE KEY `UK_Chunk_File_ChunkIndex` (`File_IDX`, `Chunk_Index`),
    KEY `IX_Chunk_File_Page` (`File_IDX`, `Page`, `Chunk_Index`),
    KEY `IX_Chunk_ChunkID` (`Chunk_ID`),

    CONSTRAINT `FK_Chunk_File`
        FOREIGN KEY (`File_IDX`)
        REFERENCES `File` (`File_IDX`)
) COMMENT 'AWS 서버 검색/응답용 청크 테이블. 원본 청크 기준은 Local RAG_Chunk';

CREATE TABLE `Rag_Purge_Task` (
                                  `Purge_Task_IDX` BIGINT NOT NULL AUTO_INCREMENT COMMENT '정리 작업 식별자(PK)',
                                  `File_IDX` BIGINT NOT NULL COMMENT '영구삭제된 파일 식별자(FK 없음)',
                                  `Users_IDX` BIGINT NOT NULL COMMENT '소유 사용자 식별자',
                                  `Status` VARCHAR(30) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING, DONE',
                                  `Attempts` INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '시도 횟수',
                                  `Next_Attempt_At` DATETIME(6) NULL COMMENT '다음 시도 시각',
                                  `Last_Error` TEXT NULL COMMENT '최근 실패 사유',
                                  `Created_At` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '생성 일시',
                                  `Updated_At` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '수정 일시',

                                  PRIMARY KEY (`Purge_Task_IDX`),
                                  KEY `IX_PurgeTask_Status_Next` (`Status`, `Next_Attempt_At`)
) COMMENT 'RAG/Qdrant 벡터 정리 재시도 아웃박스. File 참조 없음(파일은 이미 삭제됨)';

/* =========================
   13. Conversation
   설명:
     - 사용자별 채팅방 설정과 최근 활동 정보를 저장한다.
   ========================= */
CREATE TABLE `Conversation` (
    `Conversation_IDX` BIGINT NOT NULL AUTO_INCREMENT COMMENT '대화방 고유 식별자(PK)',
    `Users_IDX` BIGINT NOT NULL COMMENT '사용자 식별자(FK)',
    `Title` VARCHAR(255) NOT NULL COMMENT '대화방 제목',
    `Created_At` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '생성 일시',
    `Updated_At` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '수정 일시',
    `Last_Activity_At` DATETIME(6) NULL COMMENT '마지막 활동 일시',
    `Del` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '삭제 여부. 0=정상, 1=삭제',

    PRIMARY KEY (`Conversation_IDX`),

    KEY `IX_Conversation_Users_LastActivity` (`Users_IDX`, `Last_Activity_At`, `Del`),

    CONSTRAINT `FK_Conversation_Users`
        FOREIGN KEY (`Users_IDX`)
        REFERENCES `Users` (`Users_IDX`),
    CONSTRAINT `CK_Conversation_Del`
        CHECK (`Del` IN (0, 1))
) COMMENT '사용자 채팅방 설정 테이블';

/* =========================
   14. Conversation_Chat
   설명:
     - 실제 대화 메시지와 LLM 응답, 토큰 사용량, 라우팅 정보를 저장한다.
     - Duration_MS는 밀리초 기준 응답 처리 시간이다.
   ========================= */
CREATE TABLE `Conversation_Chat` (
    `Conversation_Chat_IDX` BIGINT NOT NULL AUTO_INCREMENT COMMENT '대화 메시지 고유 식별자(PK)',
    `Conversation_IDX` BIGINT NOT NULL COMMENT '대화방 식별자(FK)',
    `Prompt` TEXT NULL COMMENT 'LLM에 전달한 프롬프트',
    `Question` TEXT NOT NULL COMMENT '사용자 질문',
    `Answer` TEXT NULL COMMENT 'AI 답변',
    `Prompt_Tokens` INT UNSIGNED NULL COMMENT '프롬프트 토큰 수',
    `Answer_Tokens` INT UNSIGNED NULL COMMENT '답변 토큰 수',
    `Total_Tokens` INT UNSIGNED NULL COMMENT '총 토큰 수',
    `Duration_MS` BIGINT UNSIGNED NULL COMMENT '응답 처리 시간(ms)',
    `Created_At` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '생성 일시',
    `Del` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '삭제 여부. 0=정상, 1=삭제',
    `Routing_Mode` VARCHAR(30) NULL COMMENT '질문 라우팅 모드. RAG, GENERAL 등',
    `Routing_Reasoning` TEXT NULL COMMENT '라우팅 판단 근거. 민감정보 저장 금지',
    `Model_Used` VARCHAR(100) NULL COMMENT '응답 생성에 사용한 모델명',
    `Max_Result_No` JSON NULL COMMENT '검색 결과 수 또는 검색 옵션 JSON',
    `Feedback_Rating` VARCHAR(10) NULL COMMENT '답변 피드백. UP 또는 DOWN',
    `Feedback_Comment` TEXT NULL COMMENT '피드백 코멘트',
    `Feedback_At` DATETIME(6) NULL COMMENT '피드백 등록/수정 일시',


    PRIMARY KEY (`Conversation_Chat_IDX`),

    KEY `IX_ConversationChat_Conversation_Created` (`Conversation_IDX`, `Created_At`),
    KEY `IX_ConversationChat_RoutingMode` (`Routing_Mode`, `Created_At`),

    CONSTRAINT `FK_ConversationChat_Conversation`
        FOREIGN KEY (`Conversation_IDX`)
        REFERENCES `Conversation` (`Conversation_IDX`),
    CONSTRAINT `CK_ConversationChat_Del`
        CHECK (`Del` IN (0, 1)),
    CONSTRAINT `CK_ConversationChat_Token`
        CHECK ((`Prompt_Tokens` IS NULL OR `Prompt_Tokens` >= 0)
           AND (`Answer_Tokens` IS NULL OR `Answer_Tokens` >= 0)
           AND (`Total_Tokens` IS NULL OR `Total_Tokens` >= 0))
) COMMENT '실제 대화 메시지, 답변, 토큰 사용량, 라우팅 정보 저장 테이블';

/* =========================
   15. Message_Citation
   설명:
     - 메시지 답변이 참조한 서버 검색용 Chunk와 파일, 페이지, 인용 순서를 저장한다.
     - AWS Chunk 기준 인용 로그이며, Local RAG_Chunk와의 매핑은 회의 후 확정한다.
   ========================= */
CREATE TABLE `Message_Citation` (
    `Message_Citation_IDX` BIGINT NOT NULL AUTO_INCREMENT COMMENT '메시지 인용 고유 식별자(PK)',
    `Conversation_Chat_IDX` BIGINT NOT NULL COMMENT '대화 메시지 식별자(FK)',
    `Chunk_IDX` BIGINT NOT NULL COMMENT 'AWS 서버 검색용 Chunk 식별자(FK)',
    `File_IDX` BIGINT NOT NULL COMMENT '파일 식별자(FK)',
    `Page` INT UNSIGNED NULL COMMENT '페이지 번호',
    `Citation_Order` INT UNSIGNED NOT NULL DEFAULT 1 COMMENT '답변 내 인용 순서',
    `Created_At` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '생성 일시',

    PRIMARY KEY (`Message_Citation_IDX`),

    UNIQUE KEY `UK_MessageCitation_Message_Order` (`Conversation_Chat_IDX`, `Citation_Order`),
    KEY `IX_MessageCitation_Chunk` (`Chunk_IDX`),
    KEY `IX_MessageCitation_File` (`File_IDX`, `Page`),

    CONSTRAINT `FK_MessageCitation_ConversationChat`
        FOREIGN KEY (`Conversation_Chat_IDX`)
        REFERENCES `Conversation_Chat` (`Conversation_Chat_IDX`),
    CONSTRAINT `FK_MessageCitation_Chunk`
        FOREIGN KEY (`Chunk_IDX`)
        REFERENCES `Chunk` (`Chunk_IDX`),
    CONSTRAINT `FK_MessageCitation_File`
        FOREIGN KEY (`File_IDX`)
        REFERENCES `File` (`File_IDX`),
    CONSTRAINT `CK_MessageCitation_Order`
        CHECK (`Citation_Order` >= 1)
) COMMENT '답변 메시지의 근거 인용 로그 테이블';

/* =========================
   16. Reorg_Snapshot
   설명:
     - 자동 문서 정리/폴더 재구성 결과의 적용 전후 스냅샷을 저장한다.
   ========================= */
CREATE TABLE `Reorg_Snapshot` (
    `Reorg_Snapshot_IDX` BIGINT NOT NULL AUTO_INCREMENT COMMENT '정리 스냅샷 고유 식별자(PK)',
    `Users_IDX` BIGINT NOT NULL COMMENT '사용자 식별자(FK)',
    `Organize_Criteria` JSON NOT NULL COMMENT '정리 기준 JSON',
    `Snapshot_JSON` JSON NOT NULL COMMENT '정리 결과 또는 적용 대상 스냅샷 JSON',
    `Applied` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '사용자 적용 여부',
    `Created_At` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '생성 일시',

    PRIMARY KEY (`Reorg_Snapshot_IDX`),

    KEY `IX_ReorgSnapshot_Users_Created` (`Users_IDX`, `Created_At`),
    KEY `IX_ReorgSnapshot_Applied` (`Applied`, `Created_At`),

    CONSTRAINT `FK_ReorgSnapshot_Users`
        FOREIGN KEY (`Users_IDX`)
        REFERENCES `Users` (`Users_IDX`),
    CONSTRAINT `CK_ReorgSnapshot_Applied`
        CHECK (`Applied` IN (0, 1))
) COMMENT '자동 문서 정리 결과 스냅샷 테이블';

SET FOREIGN_KEY_CHECKS = 1;
