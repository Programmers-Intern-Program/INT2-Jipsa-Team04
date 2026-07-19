package com.jipsa.user;

import com.jipsa.auth.google.GoogleAuthException;
import com.jipsa.auth.google.GoogleUserInfo;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.dao.DataIntegrityViolationException;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * UserService.findOrCreate(GoogleUserInfo)의 분기 로직을 Mockito로 결정론적으로 검증한다.
 * (실제 영속화/암호화 배선은 UserRegistrationServiceTest에서 H2로 검증한다.)
 */
@ExtendWith(MockitoExtension.class)
class UserServiceTest {

    @Mock
    private UsersRepository usersRepository;
    @Mock
    private OAuthConnectionsRepository oauthRepository;
    @Mock
    private UserRegistrationService userRegistrationService;

    @InjectMocks
    private UserService userService;

    private GoogleUserInfo google;

    @BeforeEach
    void setUp() {
        google = new GoogleUserInfo("google-sub-123", "user@example.com", true, "홍길동", "http://img/p.png");
    }

    private static OAuthConnection connectionTo(long usersId) {
        OAuthConnection conn = new OAuthConnection();
        conn.setUsersId(usersId);
        conn.setProvider("GOOGLE");
        conn.setProviderUserId("google-sub-123");
        return conn;
    }

    private static Users userWith(long id, String status, boolean del) {
        Users u = new Users();
        u.setId(id);
        u.setStatus(status);
        u.setDel(del);
        return u;
    }

    @Test
    void 신규_사용자면_register후_isNewUser_true() {
        when(oauthRepository.findByProviderAndProviderUserIdAndDelFalse("GOOGLE", "google-sub-123"))
                .thenReturn(Optional.empty());
        when(oauthRepository.existsByProviderAndProviderUserId("GOOGLE", "google-sub-123"))
                .thenReturn(false);
        Users created = userWith(10L, "ACTIVE", false);
        when(userRegistrationService.register(google, "GOOGLE")).thenReturn(created);

        UserFindOrCreateResult result = userService.findOrCreate(google);

        assertThat(result.isNewUser()).isTrue();
        assertThat(result.user()).isSameAs(created);
        verify(userRegistrationService).register(google, "GOOGLE");
    }

    @Test
    void 활성_기존_사용자면_register없이_isNewUser_false() {
        when(oauthRepository.findByProviderAndProviderUserIdAndDelFalse("GOOGLE", "google-sub-123"))
                .thenReturn(Optional.of(connectionTo(5L)));
        when(usersRepository.findById(5L)).thenReturn(Optional.of(userWith(5L, "ACTIVE", false)));

        UserFindOrCreateResult result = userService.findOrCreate(google);

        assertThat(result.isNewUser()).isFalse();
        assertThat(result.user().getId()).isEqualTo(5L);
        verify(userRegistrationService, never()).register(any(), any());
    }

    @Test
    void 신규_경로에서_이름이_blank면_GoogleAuthException() {
        GoogleUserInfo noName = new GoogleUserInfo("google-sub-123", "user@example.com", true, "  ", "http://img");
        when(oauthRepository.findByProviderAndProviderUserIdAndDelFalse("GOOGLE", "google-sub-123"))
                .thenReturn(Optional.empty());

        assertThatThrownBy(() -> userService.findOrCreate(noName))
                .isInstanceOf(GoogleAuthException.class);

        verify(userRegistrationService, never()).register(any(), any());
    }

    @Test
    void 기존_사용자면_이름이_null이어도_통과한다() {
        // 이름 검증은 "신규 생성 경로"에서만 → 기존 사용자는 name null이어도 로그인 가능
        GoogleUserInfo nullName = new GoogleUserInfo("google-sub-123", "user@example.com", true, null, null);
        when(oauthRepository.findByProviderAndProviderUserIdAndDelFalse("GOOGLE", "google-sub-123"))
                .thenReturn(Optional.of(connectionTo(7L)));
        when(usersRepository.findById(7L)).thenReturn(Optional.of(userWith(7L, "ACTIVE", false)));

        UserFindOrCreateResult result = userService.findOrCreate(nullName);

        assertThat(result.isNewUser()).isFalse();
        assertThat(result.user().getId()).isEqualTo(7L);
    }

    @Test
    void 탈퇴이력_있으면_자동재가입없이_차단() {
        when(oauthRepository.findByProviderAndProviderUserIdAndDelFalse("GOOGLE", "google-sub-123"))
                .thenReturn(Optional.empty());
        when(oauthRepository.existsByProviderAndProviderUserId("GOOGLE", "google-sub-123"))
                .thenReturn(true);   // del=true 이력 존재

        assertThatThrownBy(() -> userService.findOrCreate(google))
                .isInstanceOf(AccountLoginBlockedException.class);

        verify(userRegistrationService, never()).register(any(), any());
    }

    @Test
    void 기존_사용자가_LOCKED면_차단() {
        when(oauthRepository.findByProviderAndProviderUserIdAndDelFalse("GOOGLE", "google-sub-123"))
                .thenReturn(Optional.of(connectionTo(3L)));
        when(usersRepository.findById(3L)).thenReturn(Optional.of(userWith(3L, "LOCKED", false)));

        assertThatThrownBy(() -> userService.findOrCreate(google))
                .isInstanceOf(AccountLoginBlockedException.class);
    }

    @Test
    void 기존_사용자가_WITHDRAWN이면_차단() {
        when(oauthRepository.findByProviderAndProviderUserIdAndDelFalse("GOOGLE", "google-sub-123"))
                .thenReturn(Optional.of(connectionTo(4L)));
        when(usersRepository.findById(4L)).thenReturn(Optional.of(userWith(4L, "WITHDRAWN", false)));

        assertThatThrownBy(() -> userService.findOrCreate(google))
                .isInstanceOf(AccountLoginBlockedException.class);
    }

    @Test
    void 기존_사용자가_del이면_차단() {
        when(oauthRepository.findByProviderAndProviderUserIdAndDelFalse("GOOGLE", "google-sub-123"))
                .thenReturn(Optional.of(connectionTo(8L)));
        when(usersRepository.findById(8L)).thenReturn(Optional.of(userWith(8L, "ACTIVE", true)));

        assertThatThrownBy(() -> userService.findOrCreate(google))
                .isInstanceOf(AccountLoginBlockedException.class);
    }

    @Test
    void 동시_최초_로그인_경합시_승자_사용자를_재조회해_반환() {
        // 1) 최초 조회: 없음 → 2) name ok → 3) 이력 없음 → 4) register가 unique 위반으로 실패
        when(oauthRepository.findByProviderAndProviderUserIdAndDelFalse("GOOGLE", "google-sub-123"))
                .thenReturn(Optional.empty())                       // step 1
                .thenReturn(Optional.of(connectionTo(20L)));        // catch 재조회: 승자
        when(oauthRepository.existsByProviderAndProviderUserId("GOOGLE", "google-sub-123"))
                .thenReturn(false);
        when(userRegistrationService.register(google, "GOOGLE"))
                .thenThrow(new DataIntegrityViolationException("duplicate OAuth connection"));
        when(usersRepository.findById(20L)).thenReturn(Optional.of(userWith(20L, "ACTIVE", false)));

        UserFindOrCreateResult result = userService.findOrCreate(google);

        assertThat(result.isNewUser()).isFalse();
        assertThat(result.user().getId()).isEqualTo(20L);
    }

    @Test
    void 재조회에_승자가_없으면_원래_DataIntegrityViolation을_그대로_던진다() {
        // 무조건 경합으로 단정하지 않는다: 승자가 없으면 다른 DB 무결성 오류일 수 있으므로
        // 원래 예외를 그대로 보존해 던진다(IllegalStateException으로 바꾸지 않음).
        DataIntegrityViolationException original = new DataIntegrityViolationException("FK 위반 등 실제 DB 오류");
        when(oauthRepository.findByProviderAndProviderUserIdAndDelFalse("GOOGLE", "google-sub-123"))
                .thenReturn(Optional.empty())      // step 1
                .thenReturn(Optional.empty());     // catch 재조회도 없음
        when(oauthRepository.existsByProviderAndProviderUserId("GOOGLE", "google-sub-123"))
                .thenReturn(false);
        when(userRegistrationService.register(google, "GOOGLE")).thenThrow(original);

        assertThatThrownBy(() -> userService.findOrCreate(google))
                .isSameAs(original);   // 원래 원인 보존
    }

    @Test
    void 탈퇴이력_계정은_이름이_blank여도_403으로_차단된다() {
        // 필수 수정 1: 삭제 이력 검사가 name 검사보다 먼저 → blank name이어도 GoogleAuth(401)이 아니라 403
        GoogleUserInfo blankName = new GoogleUserInfo("google-sub-123", "u@example.com", true, "  ", null);
        when(oauthRepository.findByProviderAndProviderUserIdAndDelFalse("GOOGLE", "google-sub-123"))
                .thenReturn(Optional.empty());
        when(oauthRepository.existsByProviderAndProviderUserId("GOOGLE", "google-sub-123"))
                .thenReturn(true);   // 탈퇴(del=true) 이력 존재

        assertThatThrownBy(() -> userService.findOrCreate(blankName))
                .isInstanceOf(AccountLoginBlockedException.class);   // GoogleAuthException 아님

        verify(userRegistrationService, never()).register(any(), any());
    }

    @Test
    void OAuth연결이_없는_사용자를_가리키면_예외() {
        when(oauthRepository.findByProviderAndProviderUserIdAndDelFalse("GOOGLE", "google-sub-123"))
                .thenReturn(Optional.of(connectionTo(99L)));
        when(usersRepository.findById(99L)).thenReturn(Optional.empty());

        assertThatThrownBy(() -> userService.findOrCreate(google))
                .isInstanceOf(IllegalStateException.class);
    }

    @Test
    void deprecated_문자열_오버로드는_기존동작_유지() {
        when(oauthRepository.findByProviderAndProviderUserIdAndDelFalse("GOOGLE", "sub-x"))
                .thenReturn(Optional.empty());
        when(usersRepository.save(any(Users.class))).thenAnswer(inv -> {
            Users u = inv.getArgument(0);
            u.setId(1L);
            return u;
        });

        Users user = userService.findOrCreate("GOOGLE", "sub-x");

        assertThat(user).isNotNull();
        verify(oauthRepository).save(any(OAuthConnection.class));
        // 신규 registrationService는 이 경로에서 쓰이지 않는다
        verify(userRegistrationService, never()).register(any(), any());
    }

    // --- verifyLoginable(userId): 토큰 재발급 등에서 재사용하는 상태 검사 ---

    @Test
    void verifyLoginable_ACTIVE_del아님이면_사용자_반환() {
        Users active = userWith(11L, "ACTIVE", false);
        when(usersRepository.findById(11L)).thenReturn(Optional.of(active));

        assertThat(userService.verifyLoginable(11L)).isSameAs(active);
    }

    @Test
    void verifyLoginable_del이면_403차단() {
        when(usersRepository.findById(12L)).thenReturn(Optional.of(userWith(12L, "ACTIVE", true)));

        assertThatThrownBy(() -> userService.verifyLoginable(12L))
                .isInstanceOf(AccountLoginBlockedException.class);
    }

    @Test
    void verifyLoginable_LOCKED면_403차단() {
        when(usersRepository.findById(13L)).thenReturn(Optional.of(userWith(13L, "LOCKED", false)));

        assertThatThrownBy(() -> userService.verifyLoginable(13L))
                .isInstanceOf(AccountLoginBlockedException.class);
    }

    @Test
    void verifyLoginable_SUSPENDED면_403차단() {
        when(usersRepository.findById(14L)).thenReturn(Optional.of(userWith(14L, "SUSPENDED", false)));

        assertThatThrownBy(() -> userService.verifyLoginable(14L))
                .isInstanceOf(AccountLoginBlockedException.class);
    }

    @Test
    void verifyLoginable_WITHDRAWN이면_403차단() {
        when(usersRepository.findById(15L)).thenReturn(Optional.of(userWith(15L, "WITHDRAWN", false)));

        assertThatThrownBy(() -> userService.verifyLoginable(15L))
                .isInstanceOf(AccountLoginBlockedException.class);
    }

    @Test
    void verifyLoginable_사용자가_없으면_IllegalState() {
        when(usersRepository.findById(404L)).thenReturn(Optional.empty());

        assertThatThrownBy(() -> userService.verifyLoginable(404L))
                .isInstanceOf(IllegalStateException.class);
    }
}
