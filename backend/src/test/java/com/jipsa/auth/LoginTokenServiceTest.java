package com.jipsa.auth;

import com.jipsa.user.UserFindOrCreateResult;
import com.jipsa.user.Users;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.when;

/**
 * LoginTokenService가 find-or-create 결과의 사용자로 두 토큰을 발급하고,
 * isNewUser를 신규/기존 구분 없이 그대로 결과에 전달하는지 검증한다.
 */
@ExtendWith(MockitoExtension.class)
class LoginTokenServiceTest {

    @Mock
    private JwtService jwtService;
    @Mock
    private RefreshTokenService refreshTokenService;

    @InjectMocks
    private LoginTokenService loginTokenService;

    private static UserFindOrCreateResult resultWith(long userId, boolean isNewUser, String role) {
        Users user = new Users();
        user.setId(userId);
        user.setRole(role);
        return new UserFindOrCreateResult(user, isNewUser);
    }

    @Test
    void 사용자id와_role로_access와_refresh를_발급하고_isNewUser를_전달한다_신규() {
        when(jwtService.generateToken(1L, "USERS")).thenReturn("access-token");
        when(refreshTokenService.issue(1L)).thenReturn("refresh-token");

        LoginResult result = loginTokenService.issueTokens(resultWith(1L, true, "USERS"));

        assertThat(result.accessToken()).isEqualTo("access-token");
        assertThat(result.refreshToken()).isEqualTo("refresh-token");
        assertThat(result.isNewUser()).isTrue();
    }

    @Test
    void 기존_사용자도_동일_경로로_발급하며_isNewUser는_false로_전달된다() {
        when(jwtService.generateToken(2L, "USERS")).thenReturn("access-2");
        when(refreshTokenService.issue(2L)).thenReturn("refresh-2");

        LoginResult result = loginTokenService.issueTokens(resultWith(2L, false, "USERS"));

        assertThat(result.accessToken()).isEqualTo("access-2");
        assertThat(result.refreshToken()).isEqualTo("refresh-2");
        assertThat(result.isNewUser()).isFalse();
    }

    @Test
    void ADMIN_사용자는_role_ADMIN이_실린_토큰을_발급받는다() {
        when(jwtService.generateToken(3L, "ADMIN")).thenReturn("access-admin");
        when(refreshTokenService.issue(3L)).thenReturn("refresh-3");

        LoginResult result = loginTokenService.issueTokens(resultWith(3L, false, "ADMIN"));

        assertThat(result.accessToken()).isEqualTo("access-admin");
    }
}
