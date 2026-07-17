package com.jipsa.admin;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.hibernate.annotations.CreationTimestamp;

import java.time.LocalDateTime;

/** DDL User_Sanctions 매핑. 사용자 제재(정지 등)와 해제 이력을 한 행에 함께 관리한다. */
@Entity
@Table(name = "User_Sanctions")
@Getter
@Setter
@NoArgsConstructor
public class UserSanction {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(name = "User_Sanction_IDX")
    private Long id;

    @Column(name = "Users_IDX", nullable = false)
    private Long usersId;

    @Column(name = "Sanctioned_By_Users_IDX", nullable = false)
    private Long sanctionedByUsersId;

    @Column(name = "Lifted_By_Users_IDX")
    private Long liftedByUsersId;

    @Enumerated(EnumType.STRING)
    @Column(name = "Sanction_Type", nullable = false, length = 30)
    private SanctionType sanctionType;

    @Enumerated(EnumType.STRING)
    @Column(name = "Sanction_Status", nullable = false, length = 30)
    private SanctionStatus sanctionStatus = SanctionStatus.ACTIVE;

    @Column(name = "Reason", columnDefinition = "TEXT", nullable = false)
    private String reason;

    /** 제재 해제 시 복원할 Users.Status 값 (예: ACTIVE). 정지 생성 시점에 함께 기록. */
    @Column(name = "Restore_User_Status", length = 30)
    private String restoreUserStatus;

    @CreationTimestamp
    @Column(name = "Started_At", updatable = false)
    private LocalDateTime startedAt;

    @Column(name = "Expires_At")
    private LocalDateTime expiresAt;

    @Column(name = "Lifted_At")
    private LocalDateTime liftedAt;

    @Column(name = "Lift_Reason", columnDefinition = "TEXT")
    private String liftReason;

    public UserSanction(Long usersId, Long sanctionedByUsersId, SanctionType sanctionType,
                         String reason, String restoreUserStatus, LocalDateTime expiresAt) {
        this.usersId = usersId;
        this.sanctionedByUsersId = sanctionedByUsersId;
        this.sanctionType = sanctionType;
        this.reason = reason;
        this.restoreUserStatus = restoreUserStatus;
        this.expiresAt = expiresAt;
    }
}
