package com.jipsa.auth;

import com.jipsa.auth.google.GoogleAuthException;
import com.jipsa.auth.google.GoogleIdTokenValidator;
import com.jipsa.auth.google.GoogleOAuthClient;
import com.jipsa.auth.google.GoogleTokenResponse;
import com.jipsa.auth.google.GoogleUserInfo;
import com.jipsa.common.exception.UnauthorizedException;
import com.jipsa.user.AccountLoginBlockedException;
import com.jipsa.user.UserFindOrCreateResult;
import com.jipsa.user.UserService;
import com.jipsa.user.Users;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InOrder;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.dao.DataIntegrityViolationException;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * AuthService가 1~4단계 컴포넌트를
 * exchange → validate → findOrCreate → issueTokens 순서로 연결하고,
 * 각 단계의 산출물을 다음 단계 입력으로 그대로 전달하며,
 * 어떤 단계의 예외든 임의로 변환하지 않고 그대로 전파하는지 검증한다.
 */
@ExtendWith(MockitoExtension.class)
class AuthServiceTest {

    private static final String AUTH_CODE = "auth-code-abc";
    private static final String CODE_VERIFIER = "code-verifier-abc";
    private static final String ID_TOKEN = "id-token-xyz";

    @Mock
    private GoogleOAuthClient googleOAuthClient;
    @Mock
    private GoogleIdTokenValidator googleIdTokenValidator;
    @Mock
    private UserService userService;
    @Mock
    private LoginTokenService loginTokenService;
    @Mock
    private RefreshTokenService refreshTokenService;
    @Mock
    private JwtService jwtService;

    @InjectMocks
    private AuthService authService;

    private GoogleTokenResponse tokenResponse;
    private GoogleUserInfo googleUserInfo;
    private UserFindOrCreateResult findOrCreateResult;

    @BeforeEach
    void setUp() {
        tokenResponse = new GoogleTokenResponse(
                "google-access", ID_TOKEN, "google-refresh", 3600L, "Bearer", "openid email profile");
        googleUserInfo = new GoogleUserInfo("google-sub-1", "user@example.com", true, "홍길동", "http://img/p.png");
        Users user = new Users();
        user.setId(42L);
        findOrCreateResult = new UserFindOrCreateResult(user, true);
    }

    @Test
    void 네_단계를_순서대로_연결하고_각_단계_전달값이_올바르다() {
        when(googleOAuthClient.exchangeAuthorizationCode(AUTH_CODE, CODE_VERIFIER)).thenReturn(tokenResponse);
        when(googleIdTokenValidator.validate(ID_TOKEN)).thenReturn(googleUserInfo);
        when(userService.findOrCreate(googleUserInfo)).thenReturn(findOrCreateResult);
        LoginResult expected = new LoginResult("access-jwt", "refresh-raw", true);
        when(loginTokenService.issueTokens(findOrCreateResult)).thenReturn(expected);

        LoginResult result = authService.loginWithGoogle(AUTH_CODE, CODE_VERIFIER);

        assertThat(result).isSameAs(expected);

        // 호출 순서: exchange → validate → findOrCreate → issueTokens
        InOrder order = inOrder(googleOAuthClient, googleIdTokenValidator, userService, loginTokenService);
        // 전달값 검증: authorizationCode → exchange, idToken → validate,
        //            googleUserInfo → findOrCreate, findOrCreateResult → issueTokens
        order.verify(googleOAuthClient).exchangeAuthorizationCode(AUTH_CODE, CODE_VERIFIER);
        order.verify(googleIdTokenValidator).validate(ID_TOKEN);
        order.verify(userService).findOrCreate(googleUserInfo);
        order.verify(loginTokenService).issueTokens(findOrCreateResult);
        order.verifyNoMoreInteractions();
    }

    @Test
    void exchange에서_GoogleAuthException이면_그대로_전파되고_이후단계는_호출되지_않는다() {
        GoogleAuthException original = new GoogleAuthException("Google 토큰 교환에 실패했습니다.");
        when(googleOAuthClient.exchangeAuthorizationCode(AUTH_CODE, CODE_VERIFIER)).thenThrow(original);

        assertThatThrownBy(() -> authService.loginWithGoogle(AUTH_CODE, CODE_VERIFIER))
                .isSameAs(original);

        verify(googleIdTokenValidator, never()).validate(org.mockito.ArgumentMatchers.any());
        verify(userService, never()).findOrCreate(org.mockito.ArgumentMatchers.any());
        verify(loginTokenService, never()).issueTokens(org.mockito.ArgumentMatchers.any());
    }

    @Test
    void validate에서_GoogleAuthException이면_그대로_전파된다() {
        GoogleAuthException original = new GoogleAuthException("유효하지 않은 Google id_token입니다.");
        when(googleOAuthClient.exchangeAuthorizationCode(AUTH_CODE, CODE_VERIFIER)).thenReturn(tokenResponse);
        when(googleIdTokenValidator.validate(ID_TOKEN)).thenThrow(original);

        assertThatThrownBy(() -> authService.loginWithGoogle(AUTH_CODE, CODE_VERIFIER))
                .isSameAs(original);

        verify(userService, never()).findOrCreate(org.mockito.ArgumentMatchers.any());
        verify(loginTokenService, never()).issueTokens(org.mockito.ArgumentMatchers.any());
    }

    @Test
    void findOrCreate에서_AccountLoginBlockedException이면_그대로_전파된다() {
        AccountLoginBlockedException original = new AccountLoginBlockedException("탈퇴 이력이 있는 계정입니다.");
        when(googleOAuthClient.exchangeAuthorizationCode(AUTH_CODE, CODE_VERIFIER)).thenReturn(tokenResponse);
        when(googleIdTokenValidator.validate(ID_TOKEN)).thenReturn(googleUserInfo);
        when(userService.findOrCreate(googleUserInfo)).thenThrow(original);

        assertThatThrownBy(() -> authService.loginWithGoogle(AUTH_CODE, CODE_VERIFIER))
                .isSameAs(original);

        verify(loginTokenService, never()).issueTokens(org.mockito.ArgumentMatchers.any());
    }

    @Test
    void issueTokens에서_예외가_나면_그대로_전파된다() {
        DataIntegrityViolationException original = new DataIntegrityViolationException("refresh token 저장 실패");
        when(googleOAuthClient.exchangeAuthorizationCode(AUTH_CODE, CODE_VERIFIER)).thenReturn(tokenResponse);
        when(googleIdTokenValidator.validate(ID_TOKEN)).thenReturn(googleUserInfo);
        when(userService.findOrCreate(googleUserInfo)).thenReturn(findOrCreateResult);
        when(loginTokenService.issueTokens(findOrCreateResult)).thenThrow(original);

        assertThatThrownBy(() -> authService.loginWithGoogle(AUTH_CODE, CODE_VERIFIER))
                .isSameAs(original);
    }

    // --- refreshAccessToken ---
    // 참고: @Transactional은 스프링 프록시 경유 호출에서만 적용되므로 이 단위 테스트에서는
    // 롤백이 검증되지 않는다(오케스트레이션·순서·전파만 검증). 실제 롤백은 통합 테스트 소관이다.

    private static final String REFRESH_RAW = "refresh-raw-xyz";

    @Test
    void 재발급은_validateAndTouch_verifyLoginable_generateToken_순서로_연결되고_새_AccessToken을_반환한다() {
        when(refreshTokenService.validateAndTouch(REFRESH_RAW)).thenReturn(42L);
        when(jwtService.generateToken(42L)).thenReturn("new-access-jwt");

        AccessTokenResponse result = authService.refreshAccessToken(REFRESH_RAW);

        assertThat(result.accessToken()).isEqualTo("new-access-jwt");

        // 호출 순서: validateAndTouch → verifyLoginable → generateToken,
        // 그리고 generateToken은 validateAndTouch가 준 userId로 호출된다(새 Access Token userId 검증).
        InOrder order = inOrder(refreshTokenService, userService, jwtService);
        order.verify(refreshTokenService).validateAndTouch(REFRESH_RAW);
        order.verify(userService).verifyLoginable(42L);
        order.verify(jwtService).generateToken(42L);
        order.verifyNoMoreInteractions();
    }

    @Test
    void 토큰_검증에서_Unauthorized면_전파되고_이후단계는_호출되지_않는다() {
        UnauthorizedException original = new UnauthorizedException("만료된 리프레시 토큰입니다.");
        when(refreshTokenService.validateAndTouch(REFRESH_RAW)).thenThrow(original);

        assertThatThrownBy(() -> authService.refreshAccessToken(REFRESH_RAW))
                .isSameAs(original);

        verify(userService, never()).verifyLoginable(org.mockito.ArgumentMatchers.any());
        verify(jwtService, never()).generateToken(org.mockito.ArgumentMatchers.any());
    }

    @Test
    void 사용자_상태검사에서_AccountLoginBlocked면_전파되고_AccessToken은_발급되지_않는다() {
        AccountLoginBlockedException original = new AccountLoginBlockedException("로그인할 수 없는 계정 상태입니다.");
        when(refreshTokenService.validateAndTouch(REFRESH_RAW)).thenReturn(42L);
        when(userService.verifyLoginable(42L)).thenThrow(original);

        assertThatThrownBy(() -> authService.refreshAccessToken(REFRESH_RAW))
                .isSameAs(original);

        verify(jwtService, never()).generateToken(org.mockito.ArgumentMatchers.any());
    }

    // --- logout ---

    @Test
    void 로그아웃은_revoke에만_위임하고_상태검사나_토큰발급을_하지_않는다() {
        authService.logout(REFRESH_RAW);

        verify(refreshTokenService).revoke(REFRESH_RAW);
        // 로그아웃은 계정 상태 검사·토큰 발급을 하지 않는다.
        verify(userService, never()).verifyLoginable(org.mockito.ArgumentMatchers.any());
        verify(jwtService, never()).generateToken(org.mockito.ArgumentMatchers.any());
    }

    @Test
    void 로그아웃_토큰_검증에서_Unauthorized면_그대로_전파된다() {
        UnauthorizedException original = new UnauthorizedException("유효하지 않은 리프레시 토큰입니다.");
        org.mockito.Mockito.doThrow(original).when(refreshTokenService).revoke(REFRESH_RAW);

        assertThatThrownBy(() -> authService.logout(REFRESH_RAW))
                .isSameAs(original);
    }
}
