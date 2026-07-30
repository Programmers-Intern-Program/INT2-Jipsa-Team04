package com.jipsa.chat;

import com.jipsa.user.Users;
import com.jipsa.user.UsersRepository;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class ConversationServiceTest {

    @Mock private ConversationRepository conversationRepository;
    @Mock private UsersRepository usersRepository;

    @InjectMocks private ConversationService conversationService;

    @Test
    void createUsesSmallestMissingActiveNumber() {
        when(usersRepository.findByIdForUpdate(7L)).thenReturn(Optional.of(new Users()));
        when(conversationRepository.findActiveTitlesByUsersId(7L))
                .thenReturn(List.of("대화 1", "대화 3", "프로젝트 대화", "대화 0"));
        when(conversationRepository.save(any())).thenAnswer(inv -> {
            Conversation conversation = inv.getArgument(0);
            conversation.setId(1L);
            return conversation;
        });

        ConversationResponse result = conversationService.create(7L, "   ");

        assertThat(result.title()).isEqualTo("대화 2");
    }

    @Test
    void createUsesFirstNumberWhenNoActiveNumberedTitleExists() {
        when(usersRepository.findByIdForUpdate(7L)).thenReturn(Optional.of(new Users()));
        when(conversationRepository.findActiveTitlesByUsersId(7L)).thenReturn(List.of("새 대화", "대화 01"));
        when(conversationRepository.save(any())).thenAnswer(inv -> inv.getArgument(0));

        ConversationResponse result = conversationService.create(7L, null);

        assertThat(result.title()).isEqualTo("대화 1");
    }

    @Test
    void customTitleDoesNotAllocateNumber() {
        when(conversationRepository.save(any())).thenAnswer(inv -> inv.getArgument(0));

        ConversationResponse result = conversationService.create(7L, "  프로젝트 대화  ");

        assertThat(result.title()).isEqualTo("프로젝트 대화");
    }

    @Test
    void getNotOwnedThrows() {
        when(conversationRepository.findByIdAndUsersIdAndDelFalse(1L, 7L)).thenReturn(Optional.empty());

        assertThatThrownBy(() -> conversationService.get(7L, 1L))
                .isInstanceOf(ConversationNotFoundException.class);
    }

    @Test
    void deleteSoftDeletes() {
        Conversation conversation = new Conversation(7L, "대화");
        conversation.setId(1L);
        when(conversationRepository.findByIdAndUsersIdAndDelFalse(1L, 7L)).thenReturn(Optional.of(conversation));

        conversationService.delete(7L, 1L);

        assertThat(conversation.isDel()).isTrue();
    }
}
