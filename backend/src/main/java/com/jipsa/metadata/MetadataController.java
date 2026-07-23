package com.jipsa.metadata;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/api/v1/metadata")
public class MetadataController {

    private final MetadataProperties properties;

    public MetadataController(MetadataProperties properties) {
        this.properties = properties;
    }

    @GetMapping("/document-types")
    public DocumentTypesResponse documentTypes() {
        return new DocumentTypesResponse(properties.getDocumentTypes());
    }

    public record DocumentTypesResponse(List<String> documentTypes) {
    }
}