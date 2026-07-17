package com.jipsa.job;

import com.jipsa.file.File;
import com.jipsa.file.ProcessingStage;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

@Component
public class LoggingStageExecutor implements IngestStageExecutor {

    private static final Logger log = LoggerFactory.getLogger(LoggingStageExecutor.class);

    @Override
    public void execute(ProcessingStage stage, File file) {
        log.info("[ingest] stage {} for file {}", stage, file.getId());
    }
}