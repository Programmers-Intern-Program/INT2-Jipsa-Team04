package com.jipsa.chat;

import com.jipsa.common.exception.TooManyRequestsException;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.ArrayDeque;
import java.util.Deque;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

@Component
public class ChatRateLimiter {

    private static final int MAX_REQUESTS_PER_WINDOW = 20;
    private static final long WINDOW_MS = 60_000L;

    private final Map<Long, Deque<Long>> userRequests = new ConcurrentHashMap<>();

    public void check(Long userId) {
        long now = System.currentTimeMillis();
        Deque<Long> timestamps = userRequests.computeIfAbsent(userId, key -> new ArrayDeque<>());
        synchronized (timestamps) {
            evictOld(timestamps, now);
            if (timestamps.size() >= MAX_REQUESTS_PER_WINDOW) {
                throw new TooManyRequestsException("요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.");
            }
            timestamps.addLast(now);
        }
    }

    @Scheduled(fixedDelayString = "${app.chat.rate-limit.cleanup-ms:300000}")
    public void cleanup() {
        long now = System.currentTimeMillis();
        userRequests.forEach((userId, timestamps) -> {
            synchronized (timestamps) {
                evictOld(timestamps, now);
                if (timestamps.isEmpty()) {
                    userRequests.remove(userId, timestamps);
                }
            }
        });
    }

    private void evictOld(Deque<Long> timestamps, long now) {
        while (!timestamps.isEmpty() && now - timestamps.peekFirst() > WINDOW_MS) {
            timestamps.pollFirst();
        }
    }
}