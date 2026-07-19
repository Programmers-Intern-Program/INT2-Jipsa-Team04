package com.jipsa.user;

import com.jipsa.auth.google.GoogleUserInfo;
import com.jipsa.common.crypto.AesGcmTextEncryptor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

/**
 * 신규 소셜 사용자 1명을 원자적으로 생성한다: Users + OAuth_Connections + Users_Information.
 *
 * <p>{@link UserService}의 private 메서드가 아니라 별도 Spring 빈으로 둔 이유:
 * {@link Propagation#REQUIRES_NEW}가 프록시를 거쳐 항상 새 트랜잭션을 열도록 보장하기
 * 위함이다(같은 빈 내부 self-invocation은 프록시를 안 타 REQUIRES_NEW가 무시된다).
 * 별도 트랜잭션이라, 동시 최초 로그인 경합으로 OAuth_Connections unique 제약
 * (OAuth_Provider+Provider_User_ID+Del) 위반이 나면 이 트랜잭션 전체(세 INSERT 모두)가
 * 롤백되고, 호출부({@link UserService})는 그 예외를 잡아 기존 사용자로 재조회한다.
 */
@Service
public class UserRegistrationService {

    private final UsersRepository usersRepository;
    private final OAuthConnectionsRepository oauthRepository;
    private final UsersInformationRepository usersInformationRepository;
    private final AesGcmTextEncryptor nameEncryptor;

    public UserRegistrationService(UsersRepository usersRepository,
                                   OAuthConnectionsRepository oauthRepository,
                                   UsersInformationRepository usersInformationRepository,
                                   AesGcmTextEncryptor nameEncryptor) {
        this.usersRepository = usersRepository;
        this.oauthRepository = oauthRepository;
        this.usersInformationRepository = usersInformationRepository;
        this.nameEncryptor = nameEncryptor;
    }

    /**
     * 신규 사용자 생성. Users → OAuth_Connections → Users_Information 순으로 저장하며,
     * 하나라도 실패하면 REQUIRES_NEW 트랜잭션 경계에서 전부 롤백된다.
     *
     * <p>이름 blank 검증과 상태·삭제이력 검사는 호출부에서 이미 끝난 상태로 진입한다
     * (name 검증은 신규 생성 경로에서만 수행) — 여기서는 name이 유효하다고 가정하고 암호화한다.
     *
     * @param googleUserInfo 검증된 구글 사용자 정보 (email/emailVerified는 저장하지 않음)
     * @param provider       OAuth 제공자 상수 (예: "GOOGLE")
     */
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public Users register(GoogleUserInfo googleUserInfo, String provider) {
        Users user = new Users();          // role=USERS, status=ACTIVE, del=false (DDL 기본값)
        usersRepository.save(user);        // IDENTITY id 할당

        OAuthConnection conn = new OAuthConnection();
        conn.setUsersId(user.getId());
        conn.setProvider(provider);
        conn.setProviderUserId(googleUserInfo.sub());   // email이 아닌 sub로 식별
        oauthRepository.save(conn);

        UsersInformation info = new UsersInformation();
        info.setUsersId(user.getId());
        info.setNameEnc(nameEncryptor.encrypt(googleUserInfo.name()));   // 평문 저장 금지
        info.setProfileImageUrl(googleUserInfo.picture());
        usersInformationRepository.save(info);

        return user;
    }
}
