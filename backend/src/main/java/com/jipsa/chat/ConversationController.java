package com.jipsa.chat;

import com.jipsa.common.CurrentUserProvider;
import com.jipsa.common.SuccessResponse;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/v1/conversations")
public class ConversationController {

    private final ConversationService conversationService;
    private final CurrentUserProvider currentUserProvider;

    public ConversationController(ConversationService conversationService,
                                  CurrentUserProvider currentUserProvider) {
        this.conversationService = conversationService;
        this.currentUserProvider = currentUserProvider;
    }

    @GetMapping
    public List<ConversationResponse> list() {
        Long userId = currentUserProvider.requireUserId();
        return conversationService.list(userId);
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public ConversationResponse create(@RequestBody(required = false) ConversationTitleRequest request) {
        Long userId = currentUserProvider.requireUserId();
        String title = request == null ? null : request.title();
        return conversationService.create(userId, title);
    }

    @GetMapping("/{id}")
    public ConversationResponse get(@PathVariable Long id) {
        Long userId = currentUserProvider.requireUserId();
        return conversationService.get(userId, id);
    }

    @PatchMapping("/{id}")
    public SuccessResponse rename(@PathVariable Long id, @RequestBody ConversationTitleRequest request) {
        Long userId = currentUserProvider.requireUserId();
        conversationService.rename(userId, id, request == null ? null : request.title());
        return SuccessResponse.ok();
    }

    @DeleteMapping("/{id}")
    public SuccessResponse delete(@PathVariable Long id) {
        Long userId = currentUserProvider.requireUserId();
        conversationService.delete(userId, id);
        return SuccessResponse.ok();
    }
}