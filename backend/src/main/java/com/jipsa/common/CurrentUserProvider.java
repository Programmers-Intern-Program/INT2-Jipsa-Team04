package com.jipsa.common;

import com.jipsa.common.exception.UnauthorizedException;
import com.jipsa.user.UsersRepository;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;

@Component
public class CurrentUserProvider {

    private final UsersRepository usersRepository;

    public CurrentUserProvider(UsersRepository usersRepository) {
        this.usersRepository = usersRepository;
    }

    public Long requireUserId() {
        Authentication auth = SecurityContextHolder.getContext().getAuthentication();
        if (auth != null && auth.getPrincipal() instanceof Long userId) {
            usersRepository.findByIdAndDelFalse(userId)
                    .filter(user -> "ACTIVE".equals(user.getStatus()))
                    .orElseThrow(() -> new UnauthorizedException("사용 정지되었거나 탈퇴한 계정입니다."));
            return userId;
        }
        throw new UnauthorizedException("인증이 필요합니다.");
    }
}