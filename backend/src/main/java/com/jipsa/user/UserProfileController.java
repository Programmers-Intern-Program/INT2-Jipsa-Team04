package com.jipsa.user;

import com.jipsa.common.ApiResponse;
import com.jipsa.common.CurrentUserProvider;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * GET /api/v1/users/me — 로그인한 사용자의 프로필 조회. 인증 필요(SecurityConfig의 anyRequest
 * authenticated에 걸린다).
 *
 * <p>{@link UserSettingController}와 동일하게 {@link CurrentUserProvider}로 현재 userId를 얻어
 * 위임하고, 응답은 공통 포맷 {@link ApiResponse}로 감싼다.
 */
@RestController
@RequestMapping("/api/v1/users/me")
public class UserProfileController {

    private final UserProfileService userProfileService;
    private final CurrentUserProvider currentUserProvider;

    public UserProfileController(UserProfileService userProfileService,
                                 CurrentUserProvider currentUserProvider) {
        this.userProfileService = userProfileService;
        this.currentUserProvider = currentUserProvider;
    }

    /** GET /api/v1/users/me — 현재 로그인한 사용자의 userId·name·profileImageUrl·role·status. */
    @GetMapping
    public ApiResponse<MeResponse> me() {
        Long userId = currentUserProvider.requireUserId();
        return ApiResponse.ok(userProfileService.getMe(userId));
    }
}
