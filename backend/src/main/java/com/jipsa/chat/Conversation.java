package com.jipsa.chat;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.hibernate.annotations.CreationTimestamp;
import org.hibernate.annotations.UpdateTimestamp;

import java.time.LocalDateTime;

@Entity
@Table(name = "Conversation")
@Getter
@Setter
@NoArgsConstructor
public class Conversation {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(name = "Conversation_IDX")
    private Long id;

    @Column(name = "Users_IDX", nullable = false)
    private Long usersId;

    @Column(name = "Title", nullable = false, length = 255)
    private String title;

    @CreationTimestamp
    @Column(name = "Created_At", updatable = false)
    private LocalDateTime createdAt;

    @UpdateTimestamp
    @Column(name = "Updated_At")
    private LocalDateTime updatedAt;

    @Column(name = "Last_Activity_At")
    private LocalDateTime lastActivityAt;

    @Column(name = "Del", nullable = false)
    private boolean del = false;

    public Conversation(Long usersId, String title) {
        this.usersId = usersId;
        this.title = title;
    }
}