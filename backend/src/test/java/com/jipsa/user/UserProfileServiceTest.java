package com.jipsa.user;

import com.jipsa.common.NotFoundException;
import com.jipsa.common.crypto.AesGcmTextEncryptor;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.BDDMockito.given;

/**
 * UserProfileService 단위 테스트. 리포지토리/암호화 컴포넌트를 mock으로 두고
 * 이름 복호화·실제 role/status 반환·삭제(없음) 케이스의 404를 검증한다.
 */
@ExtendWith(MockitoExtension.class)
class UserProfileServiceTest {

    private static final Long USER_ID = 1L;

    @Mock private UsersRepository usersRepository;
    @Mock private UsersInformationRepository usersInformationRepository;
    @Mock private AesGcmTextEncryptor textEncryptor;

    @InjectMocks private UserProfileService userProfileService;

    @Test
    void getMe_이름을_복호화해_실제_role_status와_함께_반환한다() {
        Users user = new Users();
        user.setId(USER_ID);
        user.setRole("ADMIN");
        user.setStatus("ACTIVE");

        UsersInformation info = new UsersInformation();
        info.setUsersId(USER_ID);
        info.setNameEnc("v1:cipher");
        info.setProfileImageUrl("https://img.example/p.png");

        given(usersRepository.findByIdAndDelFalse(USER_ID)).willReturn(Optional.of(user));
        given(usersInformationRepository.findByUsersIdAndDelFalse(USER_ID)).willReturn(Optional.of(info));
        given(textEncryptor.decrypt("v1:cipher")).willReturn("홍길동");

        MeResponse result = userProfileService.getMe(USER_ID);

        assertThat(result.userId()).isEqualTo(USER_ID);
        assertThat(result.name()).isEqualTo("홍길동");
        assertThat(result.profileImageUrl()).isEqualTo("https://img.example/p.png");
        assertThat(result.role()).isEqualTo("ADMIN");
        assertThat(result.status()).isEqualTo("ACTIVE");
    }

    @Test
    void getMe_사용자가_없거나_삭제되면_404() {
        given(usersRepository.findByIdAndDelFalse(USER_ID)).willReturn(Optional.empty());

        assertThatThrownBy(() -> userProfileService.getMe(USER_ID))
                .isInstanceOf(NotFoundException.class);
    }

    @Test
    void getMe_프로필정보가_없으면_404() {
        Users user = new Users();
        user.setId(USER_ID);
        given(usersRepository.findByIdAndDelFalse(USER_ID)).willReturn(Optional.of(user));
        given(usersInformationRepository.findByUsersIdAndDelFalse(USER_ID)).willReturn(Optional.empty());

        assertThatThrownBy(() -> userProfileService.getMe(USER_ID))
                .isInstanceOf(NotFoundException.class);
    }
}
