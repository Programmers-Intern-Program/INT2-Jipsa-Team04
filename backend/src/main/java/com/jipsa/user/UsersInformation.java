package com.jipsa.user;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.hibernate.annotations.CreationTimestamp;
import org.hibernate.annotations.UpdateTimestamp;

import java.time.LocalDateTime;

/**
 * Users와 1:1 프로필 정보(표시명·프로필 이미지). DDL {@code Users_Information} 매핑.
 *
 * <p>{@code Name_Enc}는 평문이 아니라 {@link com.jipsa.common.crypto.AesGcmTextEncryptor}로
 * 암호화한 문자열을 저장한다(NOT NULL). Google email/email_verified는 저장하지 않는다.
 */
@Entity
@Table(name = "Users_Information")   // exact table name from the DDL (case matters on Linux MySQL)
@Getter
@Setter
@NoArgsConstructor
public class UsersInformation {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(name = "Users_Information_IDX")
    private Long id;

    @Column(name = "Users_IDX", nullable = false)
    private Long usersId;             // FK to Users.Users_IDX (kept as a plain id, matching OAuthConnection)

    @Column(name = "Name_Enc", nullable = false)
    private String nameEnc;          // AES-GCM 암호문 (평문 저장 금지)

    @Column(name = "Profile_Image_URL", length = 1024)
    private String profileImageUrl;  // nullable — Google picture URL (접근 토큰 미포함)

    @CreationTimestamp
    @Column(name = "Created_At", updatable = false)
    private LocalDateTime createdAt;

    @UpdateTimestamp
    @Column(name = "Updated_At")
    private LocalDateTime updatedAt;

    @Column(name = "Del", nullable = false)
    private boolean del = false;     // TINYINT(1): 0 = active, 1 = deleted
}
