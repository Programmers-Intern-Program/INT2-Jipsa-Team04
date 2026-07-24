package com.jipsa.chat;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface MessageCitationRepository extends JpaRepository<MessageCitation, Long> {

    List<MessageCitation> findByConversationChatIdOrderByCitationOrder(Long conversationChatId);
}