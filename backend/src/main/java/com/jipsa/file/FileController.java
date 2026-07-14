package com.jipsa.file;

import com.jipsa.common.SuccessResponse;
import com.jipsa.common.CurrentUserProvider;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

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
}