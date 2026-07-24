package com.jipsa.chat;

import com.jipsa.chunk.Chunk;
import com.jipsa.chunk.ChunkRepository;
import com.jipsa.common.BadRequestException;
import com.jipsa.file.File;
import com.jipsa.file.FileRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;

@Service
public class ChatService {

    private static final int MAX_QUESTION_LENGTH = 4096;
    private static final int EXCERPT_MAX_LENGTH = 500;
    private static final int MAX_FILE_SCOPE = 50;

    private final ConversationRepository conversationRepository;
    private final ConversationChatRepository conversationChatRepository;
    private final MessageCitationRepository messageCitationRepository;
    private final ChunkRepository chunkRepository;
    private final FileRepository fileRepository;
    private final RagAnswerClient ragAnswerClient;
    private final ChatRateLimiter chatRateLimiter;

    public ChatService(ConversationRepository conversationRepository,
                       ConversationChatRepository conversationChatRepository,
                       MessageCitationRepository messageCitationRepository,
                       ChunkRepository chunkRepository,
                       FileRepository fileRepository,
                       RagAnswerClient ragAnswerClient,
                       ChatRateLimiter chatRateLimiter) {
        this.conversationRepository = conversationRepository;
        this.conversationChatRepository = conversationChatRepository;
        this.messageCitationRepository = messageCitationRepository;
        this.chunkRepository = chunkRepository;
        this.fileRepository = fileRepository;
        this.ragAnswerClient = ragAnswerClient;
        this.chatRateLimiter = chatRateLimiter;
    }

    @Transactional
    public ChatMessageResponse sendMessage(Long userId, Long conversationId, SendMessageRequest request) {
        Conversation conversation = requireOwned(userId, conversationId);
        chatRateLimiter.check(userId);
        String question = normalizeQuestion(request == null ? null : request.question());

        List<Long> fileIds = (request == null || request.fileIds() == null || request.fileIds().isEmpty())
                ? null
                : request.fileIds();
        if (fileIds != null && fileIds.size() > MAX_FILE_SCOPE) {
            throw new BadRequestException("한 번에 지정할 수 있는 문서는 최대 " + MAX_FILE_SCOPE + "개입니다.");
        }
        RagAnswerRequest ragRequest = new RagAnswerRequest(
                userId,
                question,
                request == null ? null : request.topK(),
                request == null ? null : request.scoreThreshold(),
                fileIds);

        long startedAt = System.currentTimeMillis();
        RagAnswerResponse ragResponse = ragAnswerClient.answer(ragRequest);
        long durationMs = System.currentTimeMillis() - startedAt;

        if (ragResponse == null || ragResponse.answer() == null) {
            throw new BadRequestException("RAG 답변을 받지 못했습니다.");
        }

        ConversationChat chat = new ConversationChat();
        chat.setConversationId(conversationId);
        chat.setQuestion(question);
        chat.setAnswer(ragResponse.answer());
        chat.setModelUsed(ragResponse.model());
        chat.setRoutingMode("RAG");
        chat.setDurationMs(durationMs);
        applyUsage(chat, ragResponse.usage());
        ConversationChat saved = conversationChatRepository.save(chat);

        List<ChatMessageResponse.Citation> citations = persistCitations(saved.getId(), ragResponse);
        conversation.setLastActivityAt(LocalDateTime.now());

        return new ChatMessageResponse(saved.getId(), ragResponse.answer(), ragResponse.status(), citations);
    }

    @Transactional(readOnly = true)
    public List<ChatMessageResponse> listMessages(Long userId, Long conversationId) {
        requireOwned(userId, conversationId);
        List<ChatMessageResponse> result = new ArrayList<>();
        for (ConversationChat message : conversationChatRepository.findByConversationIdAndDelFalseOrderByCreatedAt(conversationId)) {
            result.add(new ChatMessageResponse(
                    message.getId(),
                    message.getAnswer(),
                    null,
                    reconstructCitations(message.getId())));
        }
        return result;
    }

    private List<ChatMessageResponse.Citation> persistCitations(Long chatId, RagAnswerResponse response) {
        List<ChatMessageResponse.Citation> citations = new ArrayList<>();
        if (!"answered".equals(response.status()) || response.sources() == null) {
            return citations;
        }
        int order = 1;
        for (RagAnswerSource source : response.sources()) {
            Chunk chunk = chunkRepository.findByChunkIdAndFileId(source.chunkId(), source.fileIdx()).orElse(null);
            if (chunk == null) {
                continue;
            }
            MessageCitation citation = new MessageCitation();
            citation.setConversationChatId(chatId);
            citation.setChunkIdx(chunk.getId());
            citation.setFileId(source.fileIdx());
            citation.setPage(source.page());
            citation.setCitationOrder(order++);
            messageCitationRepository.save(citation);

            citations.add(new ChatMessageResponse.Citation(
                    source.fileIdx(),
                    source.fileName(),
                    source.page(),
                    source.sectionTitle(),
                    source.excerpt(),
                    source.score()));
        }
        return citations;
    }

    private List<ChatMessageResponse.Citation> reconstructCitations(Long chatId) {
        List<ChatMessageResponse.Citation> result = new ArrayList<>();
        for (MessageCitation citation : messageCitationRepository.findByConversationChatIdOrderByCitationOrder(chatId)) {
            String fileName = fileRepository.findById(citation.getFileId()).map(File::getName).orElse(null);
            String excerpt = chunkRepository.findById(citation.getChunkIdx())
                    .map(chunk -> truncate(chunk.getContent()))
                    .orElse(null);
            result.add(new ChatMessageResponse.Citation(
                    citation.getFileId(),
                    fileName,
                    citation.getPage(),
                    null,
                    excerpt,
                    null));
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

    private String truncate(String text) {
        if (text == null) {
            return null;
        }
        return text.length() > EXCERPT_MAX_LENGTH ? text.substring(0, EXCERPT_MAX_LENGTH) : text;
    }
}