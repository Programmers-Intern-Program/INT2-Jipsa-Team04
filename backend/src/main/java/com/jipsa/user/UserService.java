package com.jipsa.user;

import com.jipsa.auth.google.GoogleAuthException;
import com.jipsa.auth.google.GoogleUserInfo;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class UserService {

    /** 현재 지원하는 유일한 OAuth 제공자. (OAuthProvider enum은 신설하지 않는다) */
    static final String GOOGLE = "GOOGLE";

    /** 로그인 가능한 유일한 계정 상태. */
    private static final String STATUS_ACTIVE = "ACTIVE";

    private final UsersRepository usersRepository;
    private final OAuthConnectionsRepository oauthRepository;
    private final UserRegistrationService userRegistrationService;

    public UserService(UsersRepository usersRepository,
                       OAuthConnectionsRepository oauthRepository,
                       UserRegistrationService userRegistrationService) {
        this.usersRepository = usersRepository;
        this.oauthRepository = oauthRepository;
        this.userRegistrationService = userRegistrationService;
    }

    /**
     * 검증된 구글 사용자({@link GoogleUserInfo})로 내부 {@link Users}를 찾거나 만든다.
     *
     * <p>식별자는 email이 아니라 {@code sub}다. 처리 흐름:
     * <ol>
     *   <li>활성 연결(GOOGLE + sub + del=false)이 있으면 → 상태 검사 후 기존 사용자 반환(isNewUser=false)</li>
     *   <li>del 무관 연결 이력이 있으면(=탈퇴 이력) → {@link AccountLoginBlockedException} (name과 무관하게 차단)</li>
     *   <li>이력도 없으면, 신규 생성 경로에서만 name blank 검증 → blank면 {@link GoogleAuthException}</li>
     *   <li>→ {@link UserRegistrationService#register}로 신규 생성(isNewUser=true)</li>
     * </ol>
     *
     * <p>이 메서드 자체에는 트랜잭션을 걸지 않는다 — 신규 생성은 REQUIRES_NEW로 독립 커밋/롤백되고,
     * 동시 최초 로그인 경합 시 그 경계에서 나오는 {@link DataIntegrityViolationException}을 여기서
     * 잡아 기존 사용자로 재조회하기 위함이다.
     */
    public UserFindOrCreateResult findOrCreate(GoogleUserInfo googleUserInfo) {
        // 1) 정상 기존 사용자 (활성 연결)
        var activeConnection =
                oauthRepository.findByProviderAndProviderUserIdAndDelFalse(GOOGLE, googleUserInfo.sub());
        if (activeConnection.isPresent()) {
            Users user = loadAndVerifyLoginable(activeConnection.get().getUsersId());
            return new UserFindOrCreateResult(user, false);
        }

        // 2) 삭제 이력 차단 — del 무관 이력이 있으면 name 값과 무관하게 신규로 처리하지 않고 차단.
        //    (탈퇴 이력자는 name blank 여부보다 우선하여 403으로 막는다)
        if (oauthRepository.existsByProviderAndProviderUserId(GOOGLE, googleUserInfo.sub())) {
            throw new AccountLoginBlockedException(
                    "탈퇴 이력이 있는 계정입니다. 자동 재가입/재활성화는 지원하지 않습니다.");
        }

        // 3) name 검증 — 신규 생성 경로에서만 수행
        if (isBlank(googleUserInfo.name())) {
            throw new GoogleAuthException("구글 계정에 표시할 이름 정보가 없어 가입을 진행할 수 없습니다.");
        }

        // 4) 신규 생성 (REQUIRES_NEW) + 동시 최초 로그인 경합 처리
        try {
            Users user = userRegistrationService.register(googleUserInfo, GOOGLE);
            return new UserFindOrCreateResult(user, true);
        } catch (DataIntegrityViolationException race) {
            // 무조건 경합으로 단정하지 않는다: 활성 연결을 재조회해 "승자"가 실제로 존재할 때만
            // 경합 복구로 처리한다. 승자가 없으면 다른 DB 무결성 오류일 수 있으므로 원래 예외를
            // 그대로 다시 던져 실제 원인이 뭉개지지 않게 한다.
            OAuthConnection winner =
                    oauthRepository.findByProviderAndProviderUserIdAndDelFalse(GOOGLE, googleUserInfo.sub())
                            .orElseThrow(() -> race);
            Users user = loadAndVerifyLoginable(winner.getUsersId());
            return new UserFindOrCreateResult(user, false);
        }
    }

    /**
     * userId로 사용자를 로드하고 로그인 가능한 상태(ACTIVE, del=false)인지 검사해 반환한다.
     *
     * <p>토큰 재발급 등 이미 사용자 식별자를 확보한 흐름에서 재사용하기 위한 공개 진입점이다.
     * find-or-create 로직과 무관하며, 내부 상태 검사는 기존 {@link #loadAndVerifyLoginable}를
     * 그대로 재사용한다. 차단 시 {@link AccountLoginBlockedException}(403), 사용자가 없으면
     * {@link IllegalStateException}을 던진다.
     */
    public Users verifyLoginable(Long userId) {
        return loadAndVerifyLoginable(userId);
    }

    /** OAuth 연결이 가리키는 사용자를 로드하고, 로그인 가능한 상태(ACTIVE, del=false)인지 검사한다. */
    private Users loadAndVerifyLoginable(Long usersId) {
        Users user = usersRepository.findById(usersId)
                .orElseThrow(() -> new IllegalStateException(
                        "OAuth 연결이 존재하지 않는 사용자를 가리킵니다: " + usersId));
        if (user.isDel() || !STATUS_ACTIVE.equals(user.getStatus())) {
            throw new AccountLoginBlockedException(
                    "로그인할 수 없는 계정 상태입니다. status=" + user.getStatus() + ", del=" + user.isDel());
        }
        return user;
    }

    private static boolean isBlank(String value) {
        return value == null || value.isBlank();
    }

    /**
     * (구) 소셜 로그인 find-or-create. 신규 Google 로그인 흐름에서는 사용하지 않는다 —
     * 새 흐름은 {@link #findOrCreate(GoogleUserInfo)}를 사용하며 상태 검사·이름 암호화·
     * Users_Information 생성을 포함한다. 이 메서드는 하위 호환을 위해 남겨둔다.
     *
     * @deprecated {@link #findOrCreate(GoogleUserInfo)}를 사용할 것.
     */
    @Deprecated
    @Transactional
    public Users findOrCreate(String provider, String providerUserId) {
        return oauthRepository
                .findByProviderAndProviderUserIdAndDelFalse(provider, providerUserId)
                .map(conn -> usersRepository.findById(conn.getUsersId())
                        .orElseThrow(() -> new IllegalStateException(
                                "OAuth connection points to a missing user: " + conn.getUsersId())))
                .orElseGet(() -> createUserWithConnection(provider, providerUserId));
    }

    private Users createUserWithConnection(String provider, String providerUserId) {
        Users user = new Users();          // role=USERS, status=ACTIVE, del=false by default
        usersRepository.save(user);        // IDENTITY id is assigned here

        OAuthConnection conn = new OAuthConnection();
        conn.setUsersId(user.getId());
        conn.setProvider(provider);
        conn.setProviderUserId(providerUserId);
        oauthRepository.save(conn);

        return user;
    }
}
