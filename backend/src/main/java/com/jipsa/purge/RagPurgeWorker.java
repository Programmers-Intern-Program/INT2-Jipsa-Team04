package com.jipsa.purge;

import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
public class RagPurgeWorker {

    private final RagPurgeService ragPurgeService;

    public RagPurgeWorker(RagPurgeService ragPurgeService) {
        this.ragPurgeService = ragPurgeService;
    }

    @Scheduled(fixedDelayString = "${app.rag.purge.poll-interval-ms:30000}")
    public void drain() {
        ragPurgeService.drainOnce();
    }
}