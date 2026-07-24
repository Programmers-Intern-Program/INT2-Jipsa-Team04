package com.jipsa.chat;

import com.jipsa.chunk.Chunk;
import com.jipsa.chunk.ChunkRepository;
import com.jipsa.common.BadRequestException;
import com.jipsa.file.FileRepository;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class ChatServiceTest {

    @Mock private ConversationRepository conversationRepository;
    @Mock private ConversationChatRepository conversationChatRepository;
    @Mock private MessageCitationRepository messageCitationRepository;
    @Mock private ChunkRepository chunkRepository;
    @Mock private FileRepository fileRepository;
    @Mock private RagAnswerClient ragAnswerClient;
    @Mock private ChatRateLimiter chatRateLimiter;

    @InjectMocks private ChatService chatService;

    private Conversation ownedConversation() {
        Conversation conversation = new Conversation(7L, "대화");
        conversation.setId(1L);
        return conversation;
    }

    private RagAnswerSource source(String chunkId, Long fileIdx, Integer page) {
        return new RagAnswerSource("SOURCE-1", chunkId, 100L, fileIdx, null,
                "파일.pdf", "pdf", 3, 0.82, page, null, null, "섹션 제목", "발췌문");
    }

    private void stubSaveReturnsId() {
        when(conversationChatRepository.save(any())).thenAnswer(inv -> {
            ConversationChat chat = inv.getArgument(0);
            chat.setId(50L);
            return chat;
        });
    }

    @Test
    void answeredPersistsMessageAndMapsCitations() {
        when(conversationRepository.findByIdAndUsersIdAndDelFalse(1L, 7L)).thenReturn(Optional.of(ownedConversation()));
        stubSaveReturnsId();
        RagAnswerResponse.RagAnswerUsage usage = new RagAnswerResponse.RagAnswerUsage(100, 40);
        RagAnswerResponse response = new RagAnswerResponse("답변", "answered",
                List.of(source("uuid-1", 10L, 3)), "claude-sonnet-5", usage, "end_turn");
        when(ragAnswerClient.answer(any())).thenReturn(response);
        Chunk chunk = new Chunk();
        chunk.setId(100L);
        when(chunkRepository.findByChunkIdAndFileId("uuid-1", 10L)).thenReturn(Optional.of(chunk));

        ChatMessageResponse result = chatService.sendMessage(7L, 1L,
                new SendMessageRequest("질문", null, null, null));

        assertThat(result.answer()).isEqualTo("답변");
        assertThat(result.status()).isEqualTo("answered");
        assertThat(result.citations()).hasSize(1);
        assertThat(result.citations().get(0).fileId()).isEqualTo(10L);
        assertThat(result.citations().get(0).page()).isEqualTo(3);

        ArgumentCaptor<MessageCitation> captor = ArgumentCaptor.forClass(MessageCitation.class);
        verify(messageCitationRepository).save(captor.capture());
        assertThat(captor.getValue().getChunkIdx()).isEqualTo(100L);
        assertThat(captor.getValue().getCitationOrder()).isEqualTo(1);
    }

    @Test
    void insufficientEvidencePersistsAnswerWithoutCitations() {
        when(conversationRepository.findByIdAndUsersIdAndDelFalse(1L, 7L)).thenReturn(Optional.of(ownedConversation()));
        stubSaveReturnsId();
        when(ragAnswerClient.answer(any())).thenReturn(
                new RagAnswerResponse("근거를 찾지 못했습니다.", "insufficient_evidence", List.of(), null, null, null));

        ChatMessageResponse result = chatService.sendMessage(7L, 1L,
                new SendMessageRequest("질문", null, null, null));

        assertThat(result.status()).isEqualTo("insufficient_evidence");
        assertThat(result.citations()).isEmpty();
        verify(messageCitationRepository, never()).save(any());
    }

    @Test
    void unmappedChunkIsSkipped() {
        when(conversationRepository.findByIdAndUsersIdAndDelFalse(1L, 7L)).thenReturn(Optional.of(ownedConversation()));
        stubSaveReturnsId();
        when(ragAnswerClient.answer(any())).thenReturn(new RagAnswerResponse("답변", "answered",
                List.of(source("missing", 10L, 1)), "m", null, null));
        when(chunkRepository.findByChunkIdAndFileId("missing", 10L)).thenReturn(Optional.empty());

        ChatMessageResponse result = chatService.sendMessage(7L, 1L,
                new SendMessageRequest("질문", null, null, null));

        assertThat(result.citations()).isEmpty();
        verify(messageCitationRepository, never()).save(any());
    }

    @Test
    void notOwnedConversationThrows() {
        when(conversationRepository.findByIdAndUsersIdAndDelFalse(1L, 7L)).thenReturn(Optional.empty());

        assertThatThrownBy(() -> chatService.sendMessage(7L, 1L,
                new SendMessageRequest("질문", null, null, null)))
                .isInstanceOf(ConversationNotFoundException.class);
    }

    @Test
    void blankQuestionThrows() {
        when(conversationRepository.findByIdAndUsersIdAndDelFalse(1L, 7L)).thenReturn(Optional.of(ownedConversation()));

        assertThatThrownBy(() -> chatService.sendMessage(7L, 1L,
                new SendMessageRequest("   ", null, null, null)))
                .isInstanceOf(BadRequestException.class);
    }

    @Test
    void rateLimitExceededThrows() {
        when(conversationRepository.findByIdAndUsersIdAndDelFalse(1L, 7L)).thenReturn(Optional.of(ownedConversation()));
        doThrow(new ResponseStatusException(HttpStatus.TOO_MANY_REQUESTS, "too many"))
                .when(chatRateLimiter).check(7L);

        assertThatThrownBy(() -> chatService.sendMessage(7L, 1L,
                new SendMessageRequest("질문", null, null, null)))
                .isInstanceOf(ResponseStatusException.class);
    }

    @Test
    void feedbackStoresRating() {
        when(conversationRepository.findByIdAndUsersIdAndDelFalse(1L, 7L)).thenReturn(Optional.of(ownedConversation()));
        ConversationChat message = new ConversationChat();
        message.setId(50L);
        message.setConversationId(1L);
        when(conversationChatRepository.findById(50L)).thenReturn(Optional.of(message));

        chatService.submitFeedback(7L, 1L, 50L, "up", "좋아요");

        assertThat(message.getFeedbackRating()).isEqualTo("UP");
        assertThat(message.getFeedbackComment()).isEqualTo("좋아요");
        assertThat(message.getFeedbackAt()).isNotNull();
    }

    @Test
    void feedbackInvalidRatingThrows() {
        when(conversationRepository.findByIdAndUsersIdAndDelFalse(1L, 7L)).thenReturn(Optional.of(ownedConversation()));
        ConversationChat message = new ConversationChat();
        message.setId(50L);
        message.setConversationId(1L);
        when(conversationChatRepository.findById(50L)).thenReturn(Optional.of(message));

        assertThatThrownBy(() -> chatService.submitFeedback(7L, 1L, 50L, "maybe", null))
                .isInstanceOf(BadRequestException.class);
    }

    @Test
    void feedbackOnMessageOutsideConversationThrows() {
        when(conversationRepository.findByIdAndUsersIdAndDelFalse(1L, 7L)).thenReturn(Optional.of(ownedConversation()));
        ConversationChat message = new ConversationChat();
        message.setId(50L);
        message.setConversationId(999L);
        when(conversationChatRepository.findById(50L)).thenReturn(Optional.of(message));

        assertThatThrownBy(() -> chatService.submitFeedback(7L, 1L, 50L, "up", null))
                .isInstanceOf(ConversationNotFoundException.class);
    }
}