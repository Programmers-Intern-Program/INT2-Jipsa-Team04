package com.jipsa.chat;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;

public interface MessageCitationRepository extends JpaRepository<MessageCitation, Long> {

    List<MessageCitation> findByConversationChatIdOrderByCitationOrder(Long conversationChatId);

    @Modifying(flushAutomatically = true, clearAutomatically = true)
    @Query("delete from MessageCitation m where m.fileId = :fileId")
    void deleteByFileId(@Param("fileId") Long fileId);
}
