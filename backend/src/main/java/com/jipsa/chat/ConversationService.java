package com.jipsa.chat;

import com.jipsa.common.BadRequestException;
import com.jipsa.user.UsersRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

@Service
public class ConversationService {

    private static final Pattern NUMBERED_TITLE = Pattern.compile("^대화 ([1-9][0-9]*)$");
    private static final int MAX_TITLE_LENGTH = 255;

    private final ConversationRepository conversationRepository;
    private final UsersRepository usersRepository;

    public ConversationService(ConversationRepository conversationRepository,
                               UsersRepository usersRepository) {
        this.conversationRepository = conversationRepository;
        this.usersRepository = usersRepository;
    }

    @Transactional
    public ConversationResponse create(Long userId, String title) {
        String normalizedTitle = title == null || title.isBlank()
                ? allocateDefaultTitle(userId)
                : normalizeTitle(title);
        Conversation conversation = new Conversation(userId, normalizedTitle);
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
        conversation.setTitle(normalizeTitle(title));
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

    private String allocateDefaultTitle(Long userId) {
        usersRepository.findByIdForUpdate(userId)
                .orElseThrow(() -> new BadRequestException("사용자를 찾을 수 없습니다."));
        Set<Integer> usedNumbers = new HashSet<>();
        for (String activeTitle : conversationRepository.findActiveTitlesByUsersId(userId)) {
            Matcher matcher = NUMBERED_TITLE.matcher(activeTitle);
            if (!matcher.matches()) {
                continue;
            }
            try {
                usedNumbers.add(Integer.parseInt(matcher.group(1)));
            } catch (NumberFormatException ignored) {
            }
        }
        int next = 1;
        while (usedNumbers.contains(next)) {
            next++;
        }
        return "대화 " + next;
    }

    private String normalizeTitle(String title) {
        String trimmed = title.trim();
        return trimmed.length() > MAX_TITLE_LENGTH ? trimmed.substring(0, MAX_TITLE_LENGTH) : trimmed;
    }
}
