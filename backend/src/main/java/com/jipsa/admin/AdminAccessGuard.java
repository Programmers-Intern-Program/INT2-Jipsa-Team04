package com.jipsa.admin;

import com.jipsa.user.UsersRepository;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;

/**
 * {@code hasRole('ADMIN')}만으로는 JWT에 실린 role이 발급 시점 값으로 고정돼있어, 관리자 권한을
 * 회수해도 기존 Access Token이 만료되거나 재발급되기 전까지는 계속 관리자로 동작한다(리뷰 지적,
 * 최대 Access Token 유효기간만큼 지연될 수 있음). 이 빈은 {@code hasRole('ADMIN')} 통과 뒤
 * DB에서 현재 role을 한 번 더 확인해, role 회수가 재로그인 없이 다음 요청부터 바로 반영되게 한다.
 *
 * <p>{@code AdminController} 클래스 레벨 {@code @PreAuthorize}에서만 참조된다 — 과거
 * {@code AdminService.requireAdmin()}처럼 서비스 메서드마다 호출을 넣어야 해서 빠뜨릴 수 있는
 * 구조가 아니라, 컨트롤러의 현재/향후 모든 메서드에 자동으로 적용된다.
 */
@Component
public class AdminAccessGuard {

    private final UsersRepository usersRepository;

    public AdminAccessGuard(UsersRepository usersRepository) {
        this.usersRepository = usersRepository;
    }

    /** 현재 인증된 사용자가 지금 이 순간 DB 기준으로도 ADMIN인지 확인한다. */
    public boolean isCurrentlyAdmin() {
        Authentication auth = SecurityContextHolder.getContext().getAuthentication();
        if (auth == null || !(auth.getPrincipal() instanceof Long userId)) {
            return false;
        }
        return usersRepository.findById(userId)
                .map(user -> "ADMIN".equals(user.getRole()))
                .orElse(false);
    }
}
