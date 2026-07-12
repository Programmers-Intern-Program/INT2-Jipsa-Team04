package com.jipsa.user;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.hibernate.annotations.CreationTimestamp;
import org.hibernate.annotations.UpdateTimestamp;

import java.time.LocalDateTime;

@Entity
@Table(name = "Users")           // exact table name from the DDL (case matters on Linux MySQL)
@Getter
@Setter
@NoArgsConstructor
public class Users {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)   // AUTO_INCREMENT
    @Column(name = "Users_IDX")
    private Long id;

    @Column(name = "Locked_Until")
    private LocalDateTime lockedUntil;

    @Column(name = "Locked_Reason")
    private String lockedReason;

    @Column(name = "Role", nullable = false, length = 30)
    private String role = "USERS";      // matches DDL default

    @Column(name = "Status", nullable = false, length = 30)
    private String status = "ACTIVE";   // matches DDL default

    @CreationTimestamp
    @Column(name = "Created_At", updatable = false)
    private LocalDateTime createdAt;

    @UpdateTimestamp
    @Column(name = "Updated_At")
    private LocalDateTime updatedAt;

    @Column(name = "Del", nullable = false)
    private boolean del = false;        // TINYINT(1): 0 = active, 1 = deleted
}
