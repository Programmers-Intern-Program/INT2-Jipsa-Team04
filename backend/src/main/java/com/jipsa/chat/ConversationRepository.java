package com.jipsa.chat;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;
import java.util.Optional;

public interface ConversationRepository extends JpaRepository<Conversation, Long> {

    List<Conversation> findByUsersIdAndDelFalseOrderByLastActivityAtDesc(Long usersId);

    Optional<Conversation> findByIdAndUsersIdAndDelFalse(Long id, Long usersId);

    @Query("select c.title from Conversation c where c.usersId = :usersId and c.del = false")
    List<String> findActiveTitlesByUsersId(@Param("usersId") Long usersId);
}
