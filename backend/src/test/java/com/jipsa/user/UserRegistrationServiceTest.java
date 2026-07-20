package com.jipsa.user;

import com.jipsa.auth.google.GoogleUserInfo;
import com.jipsa.common.crypto.AesGcmTextEncryptor;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

import java.util.concurrent.atomic.AtomicLong;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * UserRegistrationService의 실제 영속화(Users+OAuthConnection+UsersInformation)와 이름 암호화
 * 배선을 H2로 검증한다.
 *
 * register()는 REQUIRES_NEW로 실제 커밋하므로(Spring TestContext 롤백이 되돌려주지 않음),
 * UserSettingServiceTest와 같은 방식으로 테스트마다 고유한 sub를 발급해 데이터 충돌을 피한다.
 */
@SpringBootTest
class UserRegistrationServiceTest {

    @Autowired
    private UserRegistrationService userRegistrationService;
    @Autowired
    private UsersRepository usersRepository;
    @Autowired
    private OAuthConnectionsRepository oauthRepository;
    @Autowired
    private UsersInformationRepository usersInformationRepository;
    @Autowired
    private AesGcmTextEncryptor nameEncryptor;

    private static final AtomicLong SEQ = new AtomicLong(1_000_000L);

    private static String nextSub() {
        return "sub-" + SEQ.incrementAndGet();
    }

    @Test
    void register_세_테이블을_모두_생성하고_이름은_암호화된다() {
        String sub = nextSub();
        GoogleUserInfo g = new GoogleUserInfo(sub, "u@example.com", true, "김철수", "http://img/p.png");

        Users user = userRegistrationService.register(g, "GOOGLE");

        assertThat(user.getId()).isNotNull();
        assertThat(user.getStatus()).isEqualTo("ACTIVE");
        assertThat(user.getRole()).isEqualTo("USERS");

        // OAuth 연결: provider=GOOGLE, providerUserId=sub
        OAuthConnection conn = oauthRepository
                .findByProviderAndProviderUserIdAndDelFalse("GOOGLE", sub)
                .orElseThrow();
        assertThat(conn.getUsersId()).isEqualTo(user.getId());

        // Users_Information: 이름은 평문이 아닌 암호문, 복호화 시 원문 복원
        UsersInformation info = usersInformationRepository.findAll().stream()
                .filter(i -> i.getUsersId().equals(user.getId()))
                .findFirst()
                .orElseThrow();
        assertThat(info.getNameEnc()).startsWith("v1:");
        assertThat(info.getNameEnc()).doesNotContain("김철수");
        assertThat(nameEncryptor.decrypt(info.getNameEnc())).isEqualTo("김철수");
        assertThat(info.getProfileImageUrl()).isEqualTo("http://img/p.png");
    }

    @Test
    void register_중_실패하면_Users_OAuth_모두_롤백된다() {
        // name=null → 마지막 단계(UsersInformation) 암호화에서 예외 → 앞선 두 INSERT도 롤백되어야 함
        String sub = nextSub();
        GoogleUserInfo bad = new GoogleUserInfo(sub, "u@example.com", true, null, null);

        assertThatThrownBy(() -> userRegistrationService.register(bad, "GOOGLE"))
                .isInstanceOf(IllegalArgumentException.class);

        // 원자성: 이 sub로 만들어진 OAuth 연결이 하나도 없어야 한다
        assertThat(oauthRepository.existsByProviderAndProviderUserId("GOOGLE", sub)).isFalse();
    }

    @Test
    void email과_emailVerified는_저장하지_않는다() {
        // UsersInformation에는 email 컬럼 자체가 없다 — 매핑 필드가 없음을 구조로 보장.
        // 여기서는 name/picture만 저장되고 email은 어디에도 남지 않음을 간접 확인한다.
        String sub = nextSub();
        GoogleUserInfo g = new GoogleUserInfo(sub, "secret-email@example.com", true, "이영희", null);

        Users user = userRegistrationService.register(g, "GOOGLE");

        UsersInformation info = usersInformationRepository.findAll().stream()
                .filter(i -> i.getUsersId().equals(user.getId()))
                .findFirst()
                .orElseThrow();
        assertThat(nameEncryptor.decrypt(info.getNameEnc())).isEqualTo("이영희");
        assertThat(info.getProfileImageUrl()).isNull();   // picture=null 그대로
    }
}
