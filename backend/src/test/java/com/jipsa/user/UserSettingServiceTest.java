package com.jipsa.user;

import jakarta.persistence.EntityManager;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.util.concurrent.atomic.AtomicLong;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * H2(create-drop) 위에서 UserSettingService를 직접 검증한다.
 * 각 테스트는 @Transactional로 감싸져 끝나면 롤백되므로 테스트 간 데이터가 섞이지 않는다.
 *
 * 단, UserSettingService.tryCreateDefaultRow()는 동시성 대응을 위해 REQUIRES_NEW로 별도
 * 트랜잭션에서 커밋한다 — Spring TestContext의 트랜잭션 롤백은 REQUIRES_NEW로 실제
 * 커밋된 내용까지 되돌려주지 않으므로(공식적으로 문서화된 한계), 테스트마다 매번
 * 새로운 userId를 발급해서 써서 이전 테스트가 커밋해 둔 행과 절대 부딪히지 않게 한다.
 * (고정된 USER=1L 같은 상수를 여러 테스트가 공유하면, 먼저 실행된 테스트가 만든 행이
 * 롤백 없이 남아있어서 뒤 테스트가 "새 유저"라고 가정한 부분이 깨진다.)
 */
@SpringBootTest
@Transactional
class UserSettingServiceTest {

    @Autowired
    private UserSettingService userSettingService;

    @Autowired
    private UserSettingRepository userSettingRepository;

    @Autowired
    private EntityManager entityManager;

    private static final AtomicLong ID_SEQ = new AtomicLong(1_000_000L);

    private static Long nextUserId() {
        return ID_SEQ.incrementAndGet();
    }

    @Test
    void getOrCreate_행이_없으면_DDL_기본값으로_생성() {
        Long user = nextUserId();

        UserSetting setting = userSettingService.getOrCreate(user);

        assertThat(setting.getUsersId()).isEqualTo(user);
        assertThat(setting.getSensitivity()).isEqualByComparingTo("0.500");
        assertThat(setting.getVoiceMode()).isEqualTo("OFF");
        assertThat(setting.getResponseStyle()).isEqualTo("BALANCED");
        assertThat(setting.isInstantSummary()).isTrue();
        assertThat(setting.isAutoHighlight()).isTrue();
        assertThat(setting.isPushNotification()).isTrue();
        assertThat(userSettingRepository.findById(user)).isPresent();
    }

    @Test
    void getOrCreate_이미_있으면_기존_행을_그대로_반환() {
        Long user = nextUserId();
        UserSetting first = userSettingService.getOrCreate(user);
        first.setVoiceMode("ON");
        userSettingRepository.save(first);

        UserSetting second = userSettingService.getOrCreate(user);

        assertThat(second.getVoiceMode()).isEqualTo("ON");
        assertThat(userSettingRepository.findById(user)).isPresent();
    }

    @Test
    void update_전달된_필드만_변경된다() {
        Long user = nextUserId();
        userSettingService.getOrCreate(user);

        userSettingService.update(user, new PatchUserSettingRequest(
                new BigDecimal("0.8"), null, null, null, null, false));

        UserSetting updated = userSettingRepository.findById(user).orElseThrow();
        assertThat(updated.getSensitivity()).isEqualByComparingTo("0.8");
        assertThat(updated.isPushNotification()).isFalse();
        // 나머지 필드는 기본값 유지
        assertThat(updated.getVoiceMode()).isEqualTo("OFF");
        assertThat(updated.getResponseStyle()).isEqualTo("BALANCED");
        assertThat(updated.isInstantSummary()).isTrue();
        assertThat(updated.isAutoHighlight()).isTrue();
    }

    @Test
    void update_빈요청은_아무것도_바꾸지않는다() {
        Long user = nextUserId();
        UserSetting before = userSettingService.getOrCreate(user);

        userSettingService.update(user, new PatchUserSettingRequest(
                null, null, null, null, null, null));

        UserSetting after = userSettingRepository.findById(user).orElseThrow();
        assertThat(after.getSensitivity()).isEqualByComparingTo(before.getSensitivity());
        assertThat(after.getVoiceMode()).isEqualTo(before.getVoiceMode());
    }

    @Test
    void update_다른유저에는_영향없음() {
        Long user = nextUserId();
        Long otherUser = nextUserId();
        userSettingService.getOrCreate(user);
        userSettingService.getOrCreate(otherUser);

        userSettingService.update(user, new PatchUserSettingRequest(
                new BigDecimal("0.9"), null, null, null, null, null));

        UserSetting other = userSettingRepository.findById(otherUser).orElseThrow();
        assertThat(other.getSensitivity()).isEqualByComparingTo("0.500");
    }

    @Test
    void update_설정행이_없어도_lazy생성후_반영된다() {
        Long user = nextUserId();

        userSettingService.update(user, new PatchUserSettingRequest(
                null, "ON", null, null, null, null));

        UserSetting setting = userSettingRepository.findById(user).orElseThrow();
        assertThat(setting.getVoiceMode()).isEqualTo("ON");
    }

    /**
     * Persistable<Long> 회귀 테스트 — 이게 깨지면 save()가 다시 merge()(SELECT 한 번 더)를
     * 타게 된다. 직접 쿼리 횟수를 세진 않고, isNew()가 의도한 시점마다 맞는 값을 주는지만
     * 확인한다(new면 true, 저장/재조회 후엔 false).
     *
     * Users_IDX는 @GeneratedValue가 없는(수동 할당) PK라 save()가 persist()를 호출해도
     * Hibernate가 INSERT를 바로 실행하지 않는다 — flush 시점까지 미뤄지고, @PostPersist도
     * 그때 실행된다. 그래서 save() 직후 바로 isNew()를 확인하려면 saveAndFlush()로 강제
     * flush해야 하고, findById가 진짜로 DB를 다시 읽어서 @PostLoad를 타는지 보려면
     * entityManager.clear()로 영속성 컨텍스트를 비워서 1차 캐시 히트를 막아야 한다.
     */
    @Test
    void isNew_새로생성한_인스턴스만_true다() {
        Long user = nextUserId();
        UserSetting fresh = new UserSetting(user);
        assertThat(fresh.isNew()).isTrue();

        UserSetting saved = userSettingRepository.saveAndFlush(fresh);
        assertThat(saved.isNew()).isFalse();

        entityManager.clear();
        UserSetting reloaded = userSettingRepository.findById(user).orElseThrow();
        assertThat(reloaded.isNew()).isFalse();
    }
}
