package com.jipsa.chat;

import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ResponseStatusException;

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
            while (!timestamps.isEmpty() && now - timestamps.peekFirst() > WINDOW_MS) {
                timestamps.pollFirst();
            }
            if (timestamps.size() >= MAX_REQUESTS_PER_WINDOW) {
                throw new ResponseStatusException(HttpStatus.TOO_MANY_REQUESTS,
                        "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.");
            }
            timestamps.addLast(now);
        }
    }
}