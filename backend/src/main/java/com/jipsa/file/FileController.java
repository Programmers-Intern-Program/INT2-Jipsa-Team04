package com.jipsa.file;

import com.jipsa.common.CurrentUserProvider;
import com.jipsa.common.SuccessResponse;
import org.springframework.core.io.Resource;
import org.springframework.http.ContentDisposition;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.nio.charset.StandardCharsets;

@RestController
@RequestMapping("/api/v1/files")
public class FileController {

    private final FileService fileService;
    private final CurrentUserProvider currentUserProvider;

    public FileController(FileService fileService, CurrentUserProvider currentUserProvider) {
        this.fileService = fileService;
        this.currentUserProvider = currentUserProvider;
    }

    @GetMapping
    public FileListResponse list(
            @RequestParam(required = false) Long folderId,
            @RequestParam(required = false) String keyword,
            @RequestParam(required = false) String docType,
            @RequestParam(defaultValue = "0") int page) {
        Long userId = currentUserProvider.requireUserId();
        return fileService.list(userId, folderId, keyword, docType, page);
    }

    @GetMapping("/{id}")
    public FileDetailResponse detail(@PathVariable Long id) {
        Long userId = currentUserProvider.requireUserId();
        return fileService.getDetail(userId, id);
    }

    @GetMapping("/{id}/status")
    public FileStatusResponse status(@PathVariable Long id) {
        Long userId = currentUserProvider.requireUserId();
        return fileService.getStatus(userId, id);
    }

    @DeleteMapping("/{id}")
    public SuccessResponse delete(@PathVariable Long id) {
        Long userId = currentUserProvider.requireUserId();
        fileService.softDelete(userId, id);
        return new SuccessResponse(true);
    }

    @PatchMapping("/{id}")
    public SuccessResponse move(@PathVariable Long id, @RequestBody MoveFileRequest request) {
        Long userId = currentUserProvider.requireUserId();
        fileService.moveToFolder(userId, id, request.folderId());
        return new SuccessResponse(true);
    }

    @PatchMapping("/{id}/star")
    public SuccessResponse star(@PathVariable Long id, @RequestBody StarRequest request) {
        Long userId = currentUserProvider.requireUserId();
        fileService.setStar(userId, id, request.star());
        return new SuccessResponse(true);
    }

    @PatchMapping("/{id}/name")
    public SuccessResponse rename(@PathVariable Long id, @RequestBody RenameRequest request) {
        Long userId = currentUserProvider.requireUserId();
        fileService.rename(userId, id, request.name());
        return new SuccessResponse(true);
    }

    @GetMapping("/{id}/download")
    public ResponseEntity<Resource> download(@PathVariable Long id) {
        Long userId = currentUserProvider.requireUserId();
        return toResponse(fileService.download(userId, id), "attachment");
    }

    @GetMapping("/{id}/view")
    public ResponseEntity<Resource> view(@PathVariable Long id) {
        Long userId = currentUserProvider.requireUserId();
        return toResponse(fileService.download(userId, id), "inline");
    }

    private ResponseEntity<Resource> toResponse(FileDownload download, String disposition) {
        ContentDisposition contentDisposition = ContentDisposition.builder(disposition)
                .filename(download.filename(), StandardCharsets.UTF_8)
                .build();
        return ResponseEntity.ok()
                .contentType(MediaType.parseMediaType(download.contentType()))
                .header(HttpHeaders.CONTENT_DISPOSITION, contentDisposition.toString())
                .contentLength(download.contentLength())
                .body(download.resource());
    }
}