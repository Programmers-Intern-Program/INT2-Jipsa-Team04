package com.jipsa.organize;

import com.jipsa.common.CurrentUserProvider;
import com.jipsa.common.SuccessResponse;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 스마트 정리(v0) API.
 * AI 제안 생성(POST /organize/propose)은 아직 없다 — AiOrganizeClient 연동 후 추가 예정.
 * 지금은 "현재 폴더 트리 조회"와 "(수동으로 구성했든 AI가 생성했든) 제안 검증 후 반영"까지만 제공한다.
 */
@RestController
@RequestMapping("/api/v1/organize")
public class OrganizeController {

    private final OrganizeService organizeService;
    private final CurrentUserProvider currentUserProvider;

    public OrganizeController(OrganizeService organizeService, CurrentUserProvider currentUserProvider) {
        this.organizeService = organizeService;
        this.currentUserProvider = currentUserProvider;
    }

    /** 미리보기의 "현재" 쪽에 쓸 본인 폴더 트리. */
    @GetMapping("/current-tree")
    public OrganizeTreeResponse currentTree() {
        Long userId = currentUserProvider.requireUserId();
        return new OrganizeTreeResponse(organizeService.getCurrentFolderTree(userId));
    }

    /** 제안(OrganizeProposal)을 검증하고, 통과하면 실제 파일 이동/이름변경 및 새 폴더 생성을 반영한다. */
    @PostMapping("/apply")
    public SuccessResponse apply(@RequestBody OrganizeProposal proposal) {
        Long userId = currentUserProvider.requireUserId();
        organizeService.applyProposal(userId, proposal);
        return new SuccessResponse(true);
    }
}
