package com.jipsa.internal;

import com.jipsa.common.exception.FileNotFoundException;
import com.jipsa.file.File;
import com.jipsa.file.FileRepository;
import com.jipsa.file.FileStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class IngestCallbackService {

    private final FileRepository fileRepository;

    public IngestCallbackService(FileRepository fileRepository) {
        this.fileRepository = fileRepository;
    }

    @Transactional
    public void complete(Long fileIdx, IngestCompleteRequest request) {
        File file = fileRepository.findByIdAndDeletedAtIsNull(fileIdx)
                .orElseThrow(() -> new FileNotFoundException("파일을 찾을 수 없습니다: " + fileIdx));
        if (request.success()) {
            file.setStatus(FileStatus.READY);
            file.setErrorMessage(null);
        } else {
            file.setStatus(FileStatus.FAILED);
            file.setErrorMessage(request.errorMessage());
        }
        file.setProcessingStage(null);
    }
}