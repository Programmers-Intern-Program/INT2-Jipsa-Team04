package com.jipsa.chat;

import com.jipsa.chunk.Chunk;
import com.jipsa.chunk.ChunkRepository;
import com.jipsa.common.BadRequestException;
import com.jipsa.file.FileRepository;
import com.jipsa.file.File;
import org.springframework.stereotype.Service;
import org.springframework.data.domain.PageRequest;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionTemplate;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.HashMap;
import java.util.Map;
import java.util.Set;

@Service
public class ChatService {

    private static final int MAX_QUESTION_LENGTH = 4096;
    private static final int MAX_REFERENCE_FILE_COUNT = 20;

    private final ConversationRepository conversationRepository;
    private final ConversationChatRepository conversationChatRepository;
    private final MessageCitationRepository messageCitationRepository;
    private final ChunkRepository chunkRepository;
    private final FileRepository fileRepository;
    private final RagAnswerClient ragAnswerClient;
    private final ChatRateLimiter chatRateLimiter;
    private final TransactionTemplate transactionTemplate;

    public ChatService(ConversationRepository conversationRepository,
                       ConversationChatRepository conversationChatRepository,
                       MessageCitationRepository messageCitationRepository,
                       ChunkRepository chunkRepository,
                       FileRepository fileRepository,
                       RagAnswerClient ragAnswerClient,
                       ChatRateLimiter chatRateLimiter,
                       PlatformTransactionManager transactionManager) {
        this.conversationRepository = conversationRepository;
        this.conversationChatRepository = conversationChatRepository;
        this.messageCitationRepository = messageCitationRepository;
        this.chunkRepository = chunkRepository;
        this.fileRepository = fileRepository;
        this.ragAnswerClient = ragAnswerClient;
        this.chatRateLimiter = chatRateLimiter;
        this.transactionTemplate = new TransactionTemplate(transactionManager);
    }

    public ChatMessageResponse sendMessage(Long userId, Long conversationId, SendMessageRequest request) {
        requireOwned(userId, conversationId);
        chatRateLimiter.check(userId);
        String question = normalizeQuestion(request == null ? null : request.question());
        List<Long> selectedFileIds = validateReferenceFileIds(userId, request == null ? null : request.fileIds());
        List<Long> ragScope = selectedFileIds.isEmpty() ? recentUserFileIds(userId) : selectedFileIds;
        if (ragScope.isEmpty()) {
            throw new BadRequestException("검색할 문서가 없습니다. 먼저 문서를 업로드해 주세요.");
        }
        Integer topK = request == null ? null : request.topK();
        Double scoreThreshold = request == null ? null : request.scoreThreshold();
        validateTopK(topK);
        validateScoreThreshold(scoreThreshold);

        RagAnswerRequest ragRequest = new RagAnswerRequest(userId, question, topK, scoreThreshold, ragScope);

        long startedAt = System.currentTimeMillis();
        RagAnswerResponse ragResponse = ragAnswerClient.answer(ragRequest);
        long durationMs = System.currentTimeMillis() - startedAt;

        if (ragResponse == null || ragResponse.answer() == null) {
            throw new BadRequestException("RAG 답변을 받지 못했습니다.");
        }

        return transactionTemplate.execute(status ->
                persistAnswer(userId, conversationId, question, selectedFileIds, ragResponse, durationMs));
    }

    private ChatMessageResponse persistAnswer(Long userId, Long conversationId, String question,
                                              List<Long> referenceFileIds,
                                              RagAnswerResponse ragResponse, long durationMs) {
        Conversation conversation = requireOwned(userId, conversationId);

        ConversationChat chat = new ConversationChat();
        chat.setConversationId(conversationId);
        chat.setQuestion(question);
        chat.setAnswer(ragResponse.answer());
        chat.setAnswerStatus(ragResponse.status());
        chat.setModelUsed(ragResponse.model());
        chat.setRoutingMode("RAG");
        chat.setDurationMs(durationMs);
        chat.setReferenceFileIds(serializeFileIds(referenceFileIds));
        applyUsage(chat, ragResponse.usage());
        ConversationChat saved = conversationChatRepository.save(chat);

        List<ChatMessageResponse.Citation> citations = persistCitations(saved.getId(), ragResponse);
        conversation.setLastActivityAt(LocalDateTime.now());

        return new ChatMessageResponse(
                saved.getId(),
                saved.getQuestion(),
                ragResponse.answer(),
                ragResponse.status(),
                saved.getFeedbackRating(),
                saved.getFeedbackComment(),
                saved.getFeedbackAt(),
                saved.getCreatedAt(),
                citations,
                resolveReferenceFiles(referenceFileIds));
    }

    @Transactional(readOnly = true)
    public List<ChatMessageResponse> listMessages(Long userId, Long conversationId) {
        requireOwned(userId, conversationId);
        List<ChatMessageResponse> result = new ArrayList<>();
        for (ConversationChat message : conversationChatRepository.findByConversationIdAndDelFalseOrderByCreatedAt(conversationId)) {
            result.add(new ChatMessageResponse(
                    message.getId(),
                    message.getQuestion(),
                    message.getAnswer(),
                    message.getAnswerStatus(),
                    message.getFeedbackRating(),
                    message.getFeedbackComment(),
                    message.getFeedbackAt(),
                    message.getCreatedAt(),
                    reconstructCitations(message.getId()),
                    resolveReferenceFiles(parseFileIds(message.getReferenceFileIds()))));
        }
        return result;
    }

    private List<Long> validateReferenceFileIds(Long userId, List<Long> fileIds) {
        if (fileIds == null || fileIds.isEmpty()) {
            return List.of();
        }
        List<Long> unique = new ArrayList<>(new LinkedHashSet<>(fileIds));
        if (unique.size() > MAX_REFERENCE_FILE_COUNT) {
            throw new BadRequestException("참조 문서는 최대 " + MAX_REFERENCE_FILE_COUNT + "개까지 지정할 수 있습니다.");
        }
        for (Long id : unique) {
            if (id == null || id <= 0) {
                throw new BadRequestException("잘못된 문서 식별자가 포함되어 있습니다.");
            }
        }
        long owned = fileRepository.countByIdInAndUsersIdAndDeletedAtIsNull(unique, userId);
        if (owned != unique.size()) {
            throw new BadRequestException("본인 소유가 아니거나 존재하지 않는 문서가 포함되어 있습니다.");
        }
        return unique;
    }

    private List<Long> recentUserFileIds(Long userId) {
        return fileRepository
                .findByUsersIdAndDeletedAtIsNullOrderByCreatedAtDesc(userId, PageRequest.of(0, MAX_REFERENCE_FILE_COUNT))
                .stream().map(File::getId).toList();
    }

    private void validateTopK(Integer topK) {
        if (topK != null && (topK < 1 || topK > 20)) {
            throw new BadRequestException("top_k는 1~20 사이여야 합니다.");
        }
    }

    private void validateScoreThreshold(Double scoreThreshold) {
        if (scoreThreshold != null && (!Double.isFinite(scoreThreshold) || scoreThreshold < -1.0 || scoreThreshold > 1.0)) {
            throw new BadRequestException("score_threshold는 -1.0~1.0 사이의 유한한 값이어야 합니다.");
        }
    }

    private List<ChatMessageResponse.Citation> persistCitations(Long chatId, RagAnswerResponse response) {
        List<ChatMessageResponse.Citation> citations = new ArrayList<>();
        if (!"answered".equals(response.status()) || response.sources() == null) {
            return citations;
        }
        Map<Long, String> currentNames = resolveCurrentFileNames(response.sources());
        int order = 1;
        for (RagAnswerSource source : response.sources()) {
            Chunk chunk = chunkRepository.findByChunkIdAndFileId(source.chunkId(), source.fileIdx()).orElse(null);
            if (chunk == null) {
                continue;
            }
            String fileName = currentNames.getOrDefault(source.fileIdx(), source.fileName());
            MessageCitation citation = new MessageCitation();
            citation.setConversationChatId(chatId);
            citation.setChunkIdx(chunk.getId());
            citation.setFileId(source.fileIdx());
            citation.setPage(source.page());
            citation.setFileName(fileName);
            citation.setSectionTitle(source.sectionTitle());
            citation.setExcerpt(source.excerpt());
            citation.setScore(source.score());
            citation.setSourceId(source.sourceId());
            citation.setCitationOrder(order++);
            messageCitationRepository.save(citation);

            citations.add(new ChatMessageResponse.Citation(
                    source.sourceId(),
                    source.fileIdx(),
                    fileName,
                    source.page(),
                    source.sectionTitle(),
                    source.excerpt(),
                    source.score()));
        }
        return citations;
    }

    private Map<Long, String> resolveCurrentFileNames(List<RagAnswerSource> sources) {
        Set<Long> ids = new LinkedHashSet<>();
        for (RagAnswerSource source : sources) {
            if (source.fileIdx() != null) {
                ids.add(source.fileIdx());
            }
        }
        Map<Long, String> names = new HashMap<>();
        if (ids.isEmpty()) {
            return names;
        }
        for (File file : fileRepository.findAllById(ids)) {
            names.put(file.getId(), file.getName());
        }
        return names;
    }

    private List<ChatMessageResponse.Citation> reconstructCitations(Long chatId) {
        List<ChatMessageResponse.Citation> result = new ArrayList<>();
        for (MessageCitation citation : messageCitationRepository.findByConversationChatIdOrderByCitationOrder(chatId)) {
            result.add(new ChatMessageResponse.Citation(
                    citation.getSourceId(),
                    citation.getFileId(),
                    citation.getFileName(),
                    citation.getPage(),
                    citation.getSectionTitle(),
                    citation.getExcerpt(),
                    citation.getScore()));
        }
        return result;
    }

    private String serializeFileIds(List<Long> ids) {
        if (ids == null || ids.isEmpty()) {
            return null;
        }
        StringBuilder builder = new StringBuilder("[");
        for (int i = 0; i < ids.size(); i++) {
            if (i > 0) {
                builder.append(",");
            }
            builder.append(ids.get(i));
        }
        return builder.append("]").toString();
    }

    private List<Long> parseFileIds(String json) {
        if (json == null || json.isBlank()) {
            return List.of();
        }
        String trimmed = json.trim();
        if (trimmed.length() <= 2) {
            return List.of();
        }
        List<Long> ids = new ArrayList<>();
        for (String part : trimmed.substring(1, trimmed.length() - 1).split(",")) {
            String value = part.trim();
            if (!value.isEmpty()) {
                ids.add(Long.parseLong(value));
            }
        }
        return ids;
    }

    private List<ChatMessageResponse.ReferenceFile> resolveReferenceFiles(List<Long> ids) {
        if (ids == null || ids.isEmpty()) {
            return List.of();
        }
        Map<Long, String> names = new HashMap<>();
        for (File file : fileRepository.findAllById(ids)) {
            names.put(file.getId(), file.getName());
        }
        List<ChatMessageResponse.ReferenceFile> result = new ArrayList<>();
        for (Long id : ids) {
            result.add(new ChatMessageResponse.ReferenceFile(id, names.get(id)));
        }
        return result;
    }

    private void applyUsage(ConversationChat chat, RagAnswerResponse.RagAnswerUsage usage) {
        if (usage == null) {
            return;
        }
        chat.setPromptTokens(usage.inputTokens());
        chat.setAnswerTokens(usage.outputTokens());
        if (usage.inputTokens() != null && usage.outputTokens() != null) {
            chat.setTotalTokens(usage.inputTokens() + usage.outputTokens());
        }
    }

    @Transactional
    public void submitFeedback(Long userId, Long conversationId, Long messageId, String rating, String comment) {
        requireOwned(userId, conversationId);
        ConversationChat message = conversationChatRepository.findById(messageId)
                .filter(m -> m.getConversationId().equals(conversationId) && !m.isDel())
                .orElseThrow(() -> new ConversationNotFoundException(conversationId));
        message.setFeedbackRating(normalizeRating(rating));
        message.setFeedbackComment(comment == null || comment.isBlank() ? null : comment.trim());
        message.setFeedbackAt(LocalDateTime.now());
    }

    private String normalizeRating(String rating) {
        if (rating == null) {
            throw new BadRequestException("피드백 값(UP/DOWN)이 필요합니다.");
        }
        String value = rating.trim().toUpperCase();
        if (!value.equals("UP") && !value.equals("DOWN")) {
            throw new BadRequestException("피드백은 UP 또는 DOWN만 가능합니다.");
        }
        return value;
    }

    private Conversation requireOwned(Long userId, Long conversationId) {
        return conversationRepository.findByIdAndUsersIdAndDelFalse(conversationId, userId)
                .orElseThrow(() -> new ConversationNotFoundException(conversationId));
    }

    private String normalizeQuestion(String question) {
        if (question == null || question.isBlank()) {
            throw new BadRequestException("질문은 비어 있을 수 없습니다.");
        }
        String trimmed = question.trim();
        if (trimmed.length() > MAX_QUESTION_LENGTH) {
            throw new BadRequestException("질문이 너무 깁니다. 최대 " + MAX_QUESTION_LENGTH + "자까지 가능합니다.");
        }
        return trimmed;
    }
}