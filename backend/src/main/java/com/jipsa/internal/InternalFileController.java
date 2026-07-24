package com.jipsa.internal;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import jakarta.validation.Valid;

@RestController
@RequestMapping("/internal/files")
public class InternalFileController {

    private final IngestManifestService ingestManifestService;
    private final IngestCallbackService ingestCallbackService;
    private final MetadataCallbackService metadataCallbackService;

    public InternalFileController(IngestManifestService ingestManifestService,
                                  IngestCallbackService ingestCallbackService,
                                  MetadataCallbackService metadataCallbackService) {
        this.ingestManifestService = ingestManifestService;
        this.ingestCallbackService = ingestCallbackService;
        this.metadataCallbackService = metadataCallbackService;
    }

    @GetMapping("/{fileIdx}/manifest")
    public ResponseEntity<IngestManifest> manifest(@PathVariable Long fileIdx) {
        return ResponseEntity.ok(ingestManifestService.build(fileIdx));
    }

    @PostMapping("/{fileIdx}/ingest-complete")
    public ResponseEntity<Void> ingestComplete(@PathVariable Long fileIdx,
                                               @Valid @RequestBody IngestCompleteRequest request) {
        ingestCallbackService.complete(fileIdx, request);
        return ResponseEntity.noContent().build();
    }

    @PostMapping("/{fileIdx}/metadata")
    public ResponseEntity<Void> metadata(@PathVariable Long fileIdx,
                                         @Valid @RequestBody IngestMetadataRequest request) {
        metadataCallbackService.apply(fileIdx, request);
        return ResponseEntity.noContent().build();
    }
}