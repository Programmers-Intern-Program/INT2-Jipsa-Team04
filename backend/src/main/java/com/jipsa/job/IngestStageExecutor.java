package com.jipsa.job;

import com.jipsa.file.File;
import com.jipsa.file.ProcessingStage;

public interface IngestStageExecutor {

    void execute(ProcessingStage stage, File file);
}