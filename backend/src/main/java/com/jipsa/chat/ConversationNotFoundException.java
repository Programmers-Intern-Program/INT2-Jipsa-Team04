package com.jipsa.chat;

import com.jipsa.common.NotFoundException;

public class ConversationNotFoundException extends NotFoundException {
    public ConversationNotFoundException(Long conversationId) {
        super("대화방을 찾을 수 없습니다: " + conversationId);
    }
}