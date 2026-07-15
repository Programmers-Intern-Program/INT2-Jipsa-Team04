package com.jipsa.user;

import lombok.extern.slf4j.Slf4j;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionTemplate;

@Slf4j
@Service
public class UserSettingService {

    private final UserSettingRepository userSettingRepository;
    private final TransactionTemplate requiresNewTransaction;

    public UserSettingService(UserSettingRepository userSettingRepository,
                               PlatformTransactionManager transactionManager) {
        this.userSettingRepository = userSettingRepository;
        this.requiresNewTransaction = new TransactionTemplate(transactionManager);
        this.requiresNewTransaction.setPropagationBehavior(TransactionDefinition.PROPAGATION_REQUIRES_NEW);
    }

    /**
     * GET /api/v1/users/me/settings — 회원가입(UserService.findOrCreate) 시점에는
     * User_Setting 행을 만들지 않으므로, 최초 조회 시 DDL 기본값으로 lazy 생성한다.
     *
     * 동시에 같은 유저가 최초 조회/수정을 두 번 이상 동시 요청하면 둘 다 "없음"을 보고
     * insert를 시도해 PK(Users_IDX) 중복이 날 수 있다 — tryCreateDefaultRow()가 그 경우를
     * 흡수한 뒤, 여기서 항상 findById로 "현재 트랜잭션에 붙어있는(managed)" 인스턴스를
     * 다시 받아온다(REQUIRES_NEW로 만든 엔티티를 그대로 반환하면 바깥 트랜잭션 기준으론
     * detached라 update()에서 필드를 바꿔도 더티체킹이 반영 안 되는 문제가 있다).
     */
    @Transactional
    public UserSetting getOrCreate(Long userId) {
        return userSettingRepository.findById(userId)
                .orElseGet(() -> {
                    DataIntegrityViolationException conflict = tryCreateDefaultRow(userId);
                    return userSettingRepository.findById(userId)
                            // conflict가 null인데 여기서 못 찾으면 insert 자체가 조용히 아무 예외 없이
                            // 실패했다는 뜻이라 정상적으로는 있을 수 없는 상태 — 방어적으로만 처리.
                            .orElseThrow(() -> conflict != null
                                    ? conflict
                                    : new IllegalStateException(
                                            "UserSetting insert 후에도 행을 찾을 수 없습니다: " + userId));
                });
    }

    /**
     * REQUIRES_NEW 별도 트랜잭션에서 기본값 행 insert를 시도한다. 별도 트랜잭션이라
     * 실패해도(경합으로 다른 요청이 먼저 커밋한 경우) 바깥(원래 조회/수정) 트랜잭션은
     * rollback-only로 오염되지 않는다.
     *
     * DataIntegrityViolationException은 두 가지 원인이 있을 수 있다: ① 정상적인 경합
     * (PK Users_IDX 중복 — 다른 요청이 먼저 같은 유저 설정을 만든 경우) ② 비정상 상황
     * (FK_UserSetting_Users 위반 — userId가 실제 Users 행을 가리키지 않는 경우, 예: JWT
     * 버그). 여기서는 구분하지 않고 일단 삼킨 뒤 예외 객체를 반환만 해둔다 — 호출부가
     * findById로 실제 행 존재 여부를 확인해서, ①이면 그 행을 쓰고 ②면 이 예외를 그대로
     * 다시 던진다("리소스 없음"으로 뭉개지 않고 원래 원인이 드러나게).
     */
    private DataIntegrityViolationException tryCreateDefaultRow(Long userId) {
        try {
            requiresNewTransaction.executeWithoutResult(status ->
                    userSettingRepository.save(new UserSetting(userId)));
            return null;
        } catch (DataIntegrityViolationException e) {
            log.warn("UserSetting 기본값 insert 실패(userId={}) — 경합으로 이미 존재하거나 FK 위반일 수 있음. "
                    + "재조회로 판별합니다.", userId, e);
            return e;
        }
    }

    /** PATCH /api/v1/users/me/settings — 전달된(null이 아닌) 필드만 부분 수정. */
    @Transactional
    public void update(Long userId, PatchUserSettingRequest request) {
        UserSetting setting = getOrCreate(userId);

        if (request.sensitivity() != null) {
            setting.setSensitivity(request.sensitivity());
        }
        if (request.voiceModel() != null) {
            setting.setVoiceMode(request.voiceModel());
        }
        if (request.responseStyle() != null) {
            setting.setResponseStyle(request.responseStyle());
        }
        if (request.instantSummary() != null) {
            setting.setInstantSummary(request.instantSummary());
        }
        if (request.autoHighlight() != null) {
            setting.setAutoHighlight(request.autoHighlight());
        }
        if (request.pushNotification() != null) {
            setting.setPushNotification(request.pushNotification());
        }
        // setting은 영속 상태 엔티티 — 트랜잭션 커밋 시 더티체킹으로 자동 반영(save 불필요).
    }
}
