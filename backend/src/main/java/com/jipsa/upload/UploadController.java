package com.jipsa.upload;

import com.jipsa.common.CurrentUserProvider;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;

@RestController
@RequestMapping("/api/v1/uploads")
public class UploadController {

    private final UploadService uploadService;
    private final CurrentUserProvider currentUserProvider;

    public UploadController(UploadService uploadService,
                            CurrentUserProvider currentUserProvider) {
        this.uploadService = uploadService;
        this.currentUserProvider = currentUserProvider;
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public UploadResponse upload(
            @RequestParam(value = "files", required = false) List<MultipartFile> files,
            @RequestParam(value = "folderId", required = false) Long folderId) {
        Long userId = currentUserProvider.requireUserId();
        return uploadService.upload(userId, files, folderId);
    }

    @GetMapping("/{id}/status")
    public UploadStatusResponse status(@PathVariable Long id) {
        Long userId = currentUserProvider.requireUserId();
        return uploadService.getStatus(userId, id);
    }
}