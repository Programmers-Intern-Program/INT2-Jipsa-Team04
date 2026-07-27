package com.jipsa.upload;

import com.jipsa.common.CurrentUserProvider;
import com.jipsa.organize.OrganizeService;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;

@RestController
@RequestMapping("/api/v1/uploads")
public class UploadController {

    private final UploadService uploadService;
    private final OrganizeService organizeService;
    private final CurrentUserProvider currentUserProvider;

    public UploadController(UploadService uploadService,
                            OrganizeService organizeService,
                            CurrentUserProvider currentUserProvider) {
        this.uploadService = uploadService;
        this.organizeService = organizeService;
        this.currentUserProvider = currentUserProvider;
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public UploadResponse upload(
            @RequestParam(value = "files", required = false) List<MultipartFile> files,
            @RequestParam(value = "folderId", required = false) Long folderId,
            @RequestParam(value = "autoRename", required = false, defaultValue = "false") boolean autoRename,
            @RequestHeader(value = "Idempotency-Key", required = false) String idempotencyKey) {
        Long userId = currentUserProvider.requireUserId();
        UploadResponse response = uploadService.upload(userId, files, folderId, idempotencyKey);
        if (autoRename) {
            organizeService.autoRenameFiles(userId, response.fileIds());
        }
        return response;
    }

    @GetMapping("/{id}/status")
    public UploadStatusResponse status(@PathVariable Long id) {
        Long userId = currentUserProvider.requireUserId();
        return uploadService.getStatus(userId, id);
    }

    @GetMapping("/recent")
    public List<RecentUploadItem> recent(
            @RequestParam(value = "limit", required = false, defaultValue = "20") int limit) {
        Long userId = currentUserProvider.requireUserId();
        return uploadService.getRecent(userId, limit);
    }
}