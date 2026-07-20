package com.jipsa.auth;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.hibernate.annotations.CreationTimestamp;
import org.hibernate.annotations.UpdateTimestamp;

import java.time.LocalDateTime;

/**
 * {@code Refresh_Tokens} 테이블 매핑. 로그인 세션 유지를 위한 Refresh Token의
 * 해시와 만료/폐기 상태를 저장한다.
 *
 * <p><b>실행 DDL(db/init/Jipsa_AWS_Server_DB_v1.sql) 기준</b>으로 매핑한다(ERD와 차이 있음):
 * 단일 PK({@code Refresh_Tokens_IDX}, IDENTITY), 삭제 여부(Del) 컬럼 없음.
 * 토큰 폐기는 {@code Revoked_At}/{@code Revoked_Reason}으로 표현한다.
 *
 * <p>원문 Refresh Token은 절대 저장하지 않는다 — {@code Token_Hash}(SHA-256)만 저장하며,
 * DDL의 {@code UK_RefreshTokens_TokenHash} unique 제약이 결정적 해시 조회를 보장한다.
 */
@Entity
@Table(name = "Refresh_Tokens")
@Getter
@Setter
@NoArgsConstructor
public class RefreshToken {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)   // AUTO_INCREMENT
    @Column(name = "Refresh_Tokens_IDX")
    private Long id;

    @Column(name = "Users_IDX", nullable = false)
    private Long usersId;               // FK to Users.Users_IDX (plain id, OAuthConnection과 동일 관례)

    @Column(name = "Expires_At", nullable = false)
    private LocalDateTime expiresAt;

    @Column(name = "Token_Hash", nullable = false, length = 255, unique = true)
    private String tokenHash;           // SHA-256 해시. 원문 저장 금지

    @Column(name = "Last_Used_At")
    private LocalDateTime lastUsedAt;

    @Column(name = "Revoked_Reason", length = 255)
    private String revokedReason;

    @Column(name = "Revoked_At")
    private LocalDateTime revokedAt;

    @CreationTimestamp
    @Column(name = "Created_At", updatable = false)
    private LocalDateTime createdAt;

    @UpdateTimestamp
    @Column(name = "Updated_At")
    private LocalDateTime updatedAt;
}
