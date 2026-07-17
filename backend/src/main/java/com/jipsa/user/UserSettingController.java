package com.jipsa.user;

import com.jipsa.common.CurrentUserProvider;
import com.jipsa.common.SuccessResponse;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/users/me/settings")
public class UserSettingController {

    private final UserSettingService userSettingService;
    private final CurrentUserProvider currentUserProvider;

    public UserSettingController(UserSettingService userSettingService, CurrentUserProvider currentUserProvider) {
        this.userSettingService = userSettingService;
        this.currentUserProvider = currentUserProvider;
    }

    /** GET /api/v1/users/me/settings — 최초 조회 시 기본값으로 자동 생성돼서 항상 200을 반환한다. */
    @GetMapping
    public UserSettingResponse get() {
        Long userId = currentUserProvider.requireUserId();
        return UserSettingResponse.from(userSettingService.getOrCreate(userId));
    }

    /** PATCH /api/v1/users/me/settings — 6개 필드 모두 선택(부분 수정). */
    @PatchMapping
    public SuccessResponse update(@Valid @RequestBody PatchUserSettingRequest request) {
        Long userId = currentUserProvider.requireUserId();
        userSettingService.update(userId, request);
        return SuccessResponse.ok();
    }
}
