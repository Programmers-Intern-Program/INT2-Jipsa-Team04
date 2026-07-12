package com.jipsa.user;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.hibernate.annotations.CreationTimestamp;
import org.hibernate.annotations.UpdateTimestamp;

import java.time.LocalDateTime;

@Entity
@Table(name = "OAuth_Connections")
@Getter
@Setter
@NoArgsConstructor
public class OAuthConnection {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(name = "OAuth_Connections_IDX")
    private Long id;

    @Column(name = "Users_IDX", nullable = false)
    private Long usersId;               // FK to Users.Users_IDX (kept as a plain id for simplicity)

    @Column(name = "OAuth_Provider", nullable = false, length = 20)
    private String provider;            // "GOOGLE", "KAKAO", "NAVER", ...

    @Column(name = "Provider_User_ID", nullable = false, length = 255)
    private String providerUserId;      // the id Google gives us for this user

    @CreationTimestamp
    @Column(name = "Created_At", updatable = false)
    private LocalDateTime createdAt;

    @UpdateTimestamp
    @Column(name = "Updated_At")
    private LocalDateTime updatedAt;

    @Column(name = "Del", nullable = false)
    private boolean del = false;
}
