package com.jipsa.internal;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.jipsa.common.exception.FileNotFoundException;
import com.jipsa.file.File;
import com.jipsa.file.FileMetadata;
import com.jipsa.file.FileMetadataRepository;
import com.jipsa.file.FileRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;

@Service
public class MetadataCallbackService {

    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();

    private final FileRepository fileRepository;
    private final FileMetadataRepository fileMetadataRepository;

    public MetadataCallbackService(FileRepository fileRepository,
                                   FileMetadataRepository fileMetadataRepository) {
        this.fileRepository = fileRepository;
        this.fileMetadataRepository = fileMetadataRepository;
    }

    @Transactional
    public void apply(Long fileIdx, IngestMetadataRequest request) {
        File file = fileRepository.findByIdAndDeletedAtIsNull(fileIdx)
                .orElseThrow(() -> new FileNotFoundException("파일을 찾을 수 없습니다: " + fileIdx));
        Integer incoming = request.indexVersion();
        LocalDateTime now = LocalDateTime.now();
        int updated;
        if (request.success()) {
            updated = fileMetadataRepository.applyCallbackSuccess(
                    fileIdx,
                    request.summary(),
                    writeJson(request.keywords()),
                    writeJson(request.entities()),
                    request.confidence(),
                    incoming,
                    now);
        } else {
            updated = fileMetadataRepository.applyCallbackFailure(fileIdx, incoming, now);
        }
        if (updated > 0 || fileMetadataRepository.existsById(fileIdx)) {
            return;
        }
        FileMetadata metadata = new FileMetadata();
        metadata.setFileId(file.getId());
        metadata.setFileType(file.getFileType());
        metadata.setExtractionIndexVersion(incoming);
        if (request.success()) {
            metadata.setSummary(request.summary());
            metadata.setKeywords(writeJson(request.keywords()));
            metadata.setExtractedEntities(writeJson(request.entities()));
            metadata.setExtractionConfidence(request.confidence());
            metadata.setExtractionStatus("READY");
        } else {
            metadata.setExtractionStatus("FAILED");
        }
        fileMetadataRepository.save(metadata);
    }

    private String writeJson(Object value) {
        if (value == null) {
            return null;
        }
        try {
            return OBJECT_MAPPER.writeValueAsString(value);
        } catch (JsonProcessingException e) {
            return null;
        }
    }
}
