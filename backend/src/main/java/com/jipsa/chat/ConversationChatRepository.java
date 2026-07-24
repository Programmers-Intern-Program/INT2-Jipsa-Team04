package com.jipsa.chat;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface ConversationChatRepository extends JpaRepository<ConversationChat, Long> {

    List<ConversationChat> findByConversationIdAndDelFalseOrderByCreatedAt(Long conversationId);
}