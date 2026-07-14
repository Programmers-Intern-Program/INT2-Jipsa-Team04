package com.jipsa.folder;

import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

@RestController
@RequestMapping("/api/v1/folders")
public class FolderController {

    private final FolderService folderService;

    public FolderController(FolderService folderService) {
        this.folderService = folderService;
    }

    /** GET /api/v1/folders — 본인 소유 전체 평면 목록. */
    @GetMapping
    public FolderListResponse list(@AuthenticationPrincipal Long userId) {
        return new FolderListResponse(folderService.list(userId));
    }

    /** POST /api/v1/folders — parentFolderId 미지정 시 루트에 생성. */
    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public CreateFolderResponse create(@AuthenticationPrincipal Long userId,
                                        @Valid @RequestBody CreateFolderRequest request) {
        Long folderId = folderService.create(userId, request.name(), request.parentFolderId());
        return new CreateFolderResponse(folderId);
    }

    /**
     * PATCH /api/v1/folders/{id} — 이름 변경 및/또는 부모 폴더 이동.
     * name, parentFolderId 둘 다 선택 필드다. 고정 DTO record 대신 Map으로 받는 이유:
     * "필드를 아예 보내지 않음(변경 없음)"과 "parentFolderId:null을 명시적으로 보냄(루트로 이동)"을
     * 구분해야 하는데, record + 기본 Jackson 역직렬화로는 두 경우 모두 null이 되어 구분이 안 된다.
     */
    @PatchMapping("/{id}")
    public SuccessResponse update(@AuthenticationPrincipal Long userId,
                                   @PathVariable Long id,
                                   @RequestBody Map<String, Object> body) {
        boolean nameProvided = body.containsKey("name");
        String name = nameProvided ? asString(body.get("name")) : null;

        boolean parentFolderIdProvided = body.containsKey("parentFolderId");
        Long parentFolderId = parentFolderIdProvided ? asLong(body.get("parentFolderId")) : null;

        folderService.update(userId, id, name, nameProvided, parentFolderId, parentFolderIdProvided);
        return SuccessResponse.ok();
    }

    /** DELETE /api/v1/folders/{id} — 하위 폴더 전부 재귀 삭제, 삭제 전 소유자 검증. */
    @DeleteMapping("/{id}")
    public SuccessResponse delete(@AuthenticationPrincipal Long userId, @PathVariable Long id) {
        folderService.delete(userId, id);
        return SuccessResponse.ok();
    }

    private String asString(Object value) {
        return value == null ? null : String.valueOf(value);
    }

    private Long asLong(Object value) {
        if (value == null) {
            return null;
        }
        if (value instanceof Number number) {
            return number.longValue();
        }
        return Long.valueOf(String.valueOf(value));
    }
}
