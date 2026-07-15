package com.jipsa.user;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.hibernate.annotations.CreationTimestamp;
import org.hibernate.annotations.UpdateTimestamp;
import org.springframework.data.domain.Persistable;

import java.math.BigDecimal;
import java.time.LocalDateTime;

/**
 * Users와 1:1 — Users_IDX 자체가 PK이자 Users로의 FK다(별도 식별자 없음, IDENTITY 아님).
 * 회원가입 시점에는 이 행이 만들어지지 않는다(OAuth 가입 플로우는 별도 작업 중) — 최초 설정
 * 조회/수정 시 UserSettingService가 DDL 기본값으로 lazy 생성한다.
 *
 * Persistable<Long>을 구현하는 이유: @GeneratedValue가 없어서(shared PK) Spring Data의
 * 기본 isNew() 판단(“ID가 null이면 새 엔티티”)이 항상 false로 나온다 — 생성자에서 이미
 * usersId를 채워서 반환하기 때문이다. 이러면 save()가 매번 persist() 대신 merge()를 타서
 * (존재 여부 확인용 SELECT 한 번 + INSERT/UPDATE) 불필요한 쿼리가 하나 더 나간다.
 * isNew()를 직접 제어해서 "생성자로 막 만든 것"과 "DB에서 읽어온 것"을 구분해주면
 * save()가 바로 persist()를 타서 그 SELECT가 없어진다.
 */
@Entity
@Table(name = "User_Setting")     // exact table name from the DDL (case matters on Linux MySQL)
@Getter
@Setter
@NoArgsConstructor
public class UserSetting implements Persistable<Long> {

    @Id
    @Column(name = "Users_IDX")
    private Long usersId;         // FK to Users.Users_IDX (shared PK, no @GeneratedValue)

    @Column(name = "Auto_Classification_Sensitivity", nullable = false, precision = 4, scale = 3)
    private BigDecimal sensitivity = new BigDecimal("0.500");   // DDL default, range 0.000~1.000

    @Column(name = "Voice_Mode", nullable = false, length = 20)
    private String voiceMode = "OFF";               // DDL default

    @Column(name = "Response_Style", nullable = false, length = 20)
    private String responseStyle = "BALANCED";       // DDL default

    @Column(name = "Instant_Summary", nullable = false)
    private boolean instantSummary = true;           // DDL default(1)

    @Column(name = "Auto_Highlight", nullable = false)
    private boolean autoHighlight = true;            // DDL default(1)

    @Column(name = "Push_Notification", nullable = false)
    private boolean pushNotification = true;         // DDL default(1)

    @CreationTimestamp
    @Column(name = "Created_At", updatable = false)
    private LocalDateTime createdAt;

    @UpdateTimestamp
    @Column(name = "Updated_At")
    private LocalDateTime updatedAt;

    /** DB에서 막 읽어왔거나(@PostLoad) 이미 한 번 저장된(@PostPersist) 이후엔 false로 바뀐다. */
    @Transient
    private boolean isNew = true;

    /** lazy 생성용 — 나머지 필드는 DDL 기본값을 그대로 사용한다. */
    public UserSetting(Long usersId) {
        this.usersId = usersId;
    }

    @Override
    public Long getId() {
        return usersId;
    }

    @Override
    public boolean isNew() {
        return isNew;
    }

    @PostLoad
    @PostPersist
    void markNotNew() {
        this.isNew = false;
    }
}
