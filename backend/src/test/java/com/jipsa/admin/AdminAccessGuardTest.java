package com.jipsa.admin;

import com.jipsa.user.Users;
import com.jipsa.user.UsersRepository;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.when;

/**
 * AdminAccessGuard가 SecurityContext의 principal(userId)로 DB를 다시 조회해 "지금 이 순간"의
 * role을 정확히 판단하는지 검증한다. JWT 클레임과 무관하게 DB 기준으로만 판단해야 한다 — 이게
 * 이 빈을 만든 이유(권한 회수 즉시 반영)다.
 */
@ExtendWith(MockitoExtension.class)
class AdminAccessGuardTest {

    @Mock
    private UsersRepository usersRepository;

    @AfterEach
    void clearContext() {
        SecurityContextHolder.clearContext();
    }

    private void authenticateAs(Long userId) {
        var auth = new UsernamePasswordAuthenticationToken(userId, null, java.util.List.of());
        SecurityContextHolder.getContext().setAuthentication(auth);
    }

    @Test
    void 인증정보가_없으면_false() {
        AdminAccessGuard guard = new AdminAccessGuard(usersRepository);

        assertThat(guard.isCurrentlyAdmin()).isFalse();
    }

    @Test
    void DB의_role이_ADMIN이면_true() {
        authenticateAs(1L);
        Users user = new Users();
        user.setId(1L);
        user.setRole("ADMIN");
        when(usersRepository.findById(1L)).thenReturn(Optional.of(user));
        AdminAccessGuard guard = new AdminAccessGuard(usersRepository);

        assertThat(guard.isCurrentlyAdmin()).isTrue();
    }

    @Test
    void DB의_role이_USERS면_false_JWT가_ADMIN이었더라도_DB기준으로_판단() {
        authenticateAs(1L);
        Users user = new Users();
        user.setId(1L);
        user.setRole("USERS"); // 관리자 권한이 방금 회수된 상황을 시뮬레이션
        when(usersRepository.findById(1L)).thenReturn(Optional.of(user));
        AdminAccessGuard guard = new AdminAccessGuard(usersRepository);

        assertThat(guard.isCurrentlyAdmin()).isFalse();
    }

    @Test
    void DB에_사용자가_없으면_false() {
        authenticateAs(999L);
        when(usersRepository.findById(999L)).thenReturn(Optional.empty());
        AdminAccessGuard guard = new AdminAccessGuard(usersRepository);

        assertThat(guard.isCurrentlyAdmin()).isFalse();
    }
}
