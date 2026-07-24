package com.jipsa.chat;

import com.jipsa.common.BadRequestException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;

@Service
public class ConversationService {

    private static final String DEFAULT_TITLE = "새 대화";
    private static final int MAX_TITLE_LENGTH = 255;

    private final ConversationRepository conversationRepository;

    public ConversationService(ConversationRepository conversationRepository) {
        this.conversationRepository = conversationRepository;
    }

    @Transactional
    public ConversationResponse create(Long userId, String title) {
        Conversation conversation = new Conversation(userId, normalizeTitle(title, DEFAULT_TITLE));
        conversation.setLastActivityAt(LocalDateTime.now());
        return ConversationResponse.from(conversationRepository.save(conversation));
    }

    @Transactional(readOnly = true)
    public List<ConversationResponse> list(Long userId) {
        return conversationRepository.findByUsersIdAndDelFalseOrderByLastActivityAtDesc(userId).stream()
                .map(ConversationResponse::from)
                .toList();
    }

    @Transactional(readOnly = true)
    public ConversationResponse get(Long userId, Long conversationId) {
        return ConversationResponse.from(requireOwned(userId, conversationId));
    }

    @Transactional
    public void rename(Long userId, Long conversationId, String title) {
        if (title == null || title.isBlank()) {
            throw new BadRequestException("대화방 제목은 비어 있을 수 없습니다.");
        }
        Conversation conversation = requireOwned(userId, conversationId);
        conversation.setTitle(normalizeTitle(title, DEFAULT_TITLE));
    }

    @Transactional
    public void delete(Long userId, Long conversationId) {
        Conversation conversation = requireOwned(userId, conversationId);
        conversation.setDel(true);
    }

    private Conversation requireOwned(Long userId, Long conversationId) {
        return conversationRepository.findByIdAndUsersIdAndDelFalse(conversationId, userId)
                .orElseThrow(() -> new ConversationNotFoundException(conversationId));
    }

    private String normalizeTitle(String title, String fallback) {
        if (title == null || title.isBlank()) {
            return fallback;
        }
        String trimmed = title.trim();
        return trimmed.length() > MAX_TITLE_LENGTH ? trimmed.substring(0, MAX_TITLE_LENGTH) : trimmed;
    }
}