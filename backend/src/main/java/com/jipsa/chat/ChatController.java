package com.jipsa.chat;

import com.jipsa.common.CurrentUserProvider;
import com.jipsa.common.SuccessResponse;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/v1/conversations/{conversationId}/messages")
public class ChatController {

    private final ChatService chatService;
    private final CurrentUserProvider currentUserProvider;

    public ChatController(ChatService chatService, CurrentUserProvider currentUserProvider) {
        this.chatService = chatService;
        this.currentUserProvider = currentUserProvider;
    }

    @PostMapping
    public ChatMessageResponse send(@PathVariable Long conversationId,
                                    @RequestBody SendMessageRequest request) {
        Long userId = currentUserProvider.requireUserId();
        return chatService.sendMessage(userId, conversationId, request);
    }

    @GetMapping
    public List<ChatMessageResponse> list(@PathVariable Long conversationId) {
        Long userId = currentUserProvider.requireUserId();
        return chatService.listMessages(userId, conversationId);
    }

    @PatchMapping("/{messageId}/feedback")
    public SuccessResponse feedback(@PathVariable Long conversationId,
                                    @PathVariable Long messageId,
                                    @RequestBody FeedbackRequest request) {
        Long userId = currentUserProvider.requireUserId();
        chatService.submitFeedback(userId, conversationId, messageId,
                request == null ? null : request.rating(),
                request == null ? null : request.comment());
        return SuccessResponse.ok();
    }
}