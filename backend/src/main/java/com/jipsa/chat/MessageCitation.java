package com.jipsa.chat;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.hibernate.annotations.CreationTimestamp;

import java.time.LocalDateTime;

@Entity
@Table(name = "Message_Citation")
@Getter
@Setter
@NoArgsConstructor
public class MessageCitation {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(name = "Message_Citation_IDX")
    private Long id;

    @Column(name = "Conversation_Chat_IDX", nullable = false)
    private Long conversationChatId;

    @Column(name = "Chunk_IDX", nullable = false)
    private Long chunkIdx;

    @Column(name = "File_IDX", nullable = false)
    private Long fileId;

    @Column(name = "Page")
    private Integer page;

    @Column(name = "File_Name", length = 255)
    private String fileName;

    @Column(name = "Section_Title", length = 500)
    private String sectionTitle;

    @Column(name = "Excerpt", columnDefinition = "TEXT")
    private String excerpt;

    @Column(name = "Score")
    private Double score;

    @Column(name = "Citation_Order", nullable = false)
    private Integer citationOrder;

    @CreationTimestamp
    @Column(name = "Created_At", updatable = false)
    private LocalDateTime createdAt;
}