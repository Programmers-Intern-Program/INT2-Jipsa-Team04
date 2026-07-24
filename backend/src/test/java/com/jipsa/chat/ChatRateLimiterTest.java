package com.jipsa.chat;

import com.jipsa.common.exception.TooManyRequestsException;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class ChatRateLimiterTest {

    @Test
    void allowsUpToLimitThenThrows() {
        ChatRateLimiter limiter = new ChatRateLimiter();
        for (int i = 0; i < 20; i++) {
            limiter.check(1L);
        }
        assertThatThrownBy(() -> limiter.check(1L))
                .isInstanceOf(TooManyRequestsException.class);
    }

    @Test
    void separateUsersHaveIndependentLimits() {
        ChatRateLimiter limiter = new ChatRateLimiter();
        for (int i = 0; i < 20; i++) {
            limiter.check(1L);
        }
        assertThatCode(() -> limiter.check(2L)).doesNotThrowAnyException();
    }
}