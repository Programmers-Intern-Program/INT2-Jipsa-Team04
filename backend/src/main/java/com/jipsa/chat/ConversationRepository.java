package com.jipsa.chat;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface ConversationRepository extends JpaRepository<Conversation, Long> {

    List<Conversation> findByUsersIdAndDelFalseOrderByLastActivityAtDesc(Long usersId);

    Optional<Conversation> findByIdAndUsersIdAndDelFalse(Long id, Long usersId);
}