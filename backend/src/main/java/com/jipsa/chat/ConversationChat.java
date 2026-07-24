package com.jipsa.chat;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.hibernate.annotations.CreationTimestamp;

import java.time.LocalDateTime;

@Entity
@Table(name = "Conversation_Chat")
@Getter
@Setter
@NoArgsConstructor
public class ConversationChat {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(name = "Conversation_Chat_IDX")
    private Long id;

    @Column(name = "Conversation_IDX", nullable = false)
    private Long conversationId;

    @Column(name = "Prompt", columnDefinition = "TEXT")
    private String prompt;

    @Column(name = "Question", nullable = false, columnDefinition = "TEXT")
    private String question;

    @Column(name = "Answer", columnDefinition = "TEXT")
    private String answer;

    @Column(name = "Prompt_Tokens")
    private Integer promptTokens;

    @Column(name = "Answer_Tokens")
    private Integer answerTokens;

    @Column(name = "Total_Tokens")
    private Integer totalTokens;

    @Column(name = "Duration_MS")
    private Long durationMs;

    @CreationTimestamp
    @Column(name = "Created_At", updatable = false)
    private LocalDateTime createdAt;

    @Column(name = "Del", nullable = false)
    private boolean del = false;

    @Column(name = "Routing_Mode", length = 30)
    private String routingMode;

    @Column(name = "Routing_Reasoning", columnDefinition = "TEXT")
    private String routingReasoning;

    @Column(name = "Model_Used", length = 100)
    private String modelUsed;

    @Column(name = "Max_Result_No", columnDefinition = "JSON")
    private String maxResultNo;

    @Column(name = "Feedback_Rating", length = 10)
    private String feedbackRating;

    @Column(name = "Feedback_Comment", columnDefinition = "TEXT")
    private String feedbackComment;

    @Column(name = "Feedback_At")
    private LocalDateTime feedbackAt;
}