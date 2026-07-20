package com.jipsa.user;

import com.jipsa.common.NotFoundException;
import com.jipsa.common.crypto.AesGcmTextEncryptor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * GET /api/v1/users/me — 로그인한 사용자의 프로필 조회(읽기 전용).
 *
 * <p>OAuth 로그인/refresh/logout 흐름과 분리된 조회 전용 서비스다. 인증 필터가 SecurityContext에
 * 넣어둔 현재 userId(=JWT subject)를 입력으로 받아 {@link Users} + {@link UsersInformation}을
 * 조회한다. {@code Name_Enc}는 {@link AesGcmTextEncryptor}로 복호화해 평문 이름으로 반환한다.
 *
 * <p>삭제되지 않은({@code Del=false}) 행만 조회 대상이다 — 소프트 삭제(관리자 삭제/탈퇴)로 행이
 * 없거나 사용자가 애초에 없으면 {@link NotFoundException}(404)을 던진다(기존 예외 스타일).
 */
@Service
public class UserProfileService {

    private final UsersRepository usersRepository;
    private final UsersInformationRepository usersInformationRepository;
    private final AesGcmTextEncryptor textEncryptor;

    public UserProfileService(UsersRepository usersRepository,
                              UsersInformationRepository usersInformationRepository,
                              AesGcmTextEncryptor textEncryptor) {
        this.usersRepository = usersRepository;
        this.usersInformationRepository = usersInformationRepository;
        this.textEncryptor = textEncryptor;
    }

    /**
     * 현재 userId로 프로필을 조회해 {@link MeResponse}로 조립한다.
     *
     * @param userId 인증 필터가 확인한 현재 사용자 (Users.Users_IDX, JWT subject)
     * @return userId·name(복호화)·profileImageUrl·role·status
     * @throws NotFoundException 사용자 또는 프로필 정보가 없거나 삭제(Del=true)된 경우
     */
    @Transactional(readOnly = true)
    public MeResponse getMe(Long userId) {
        Users user = usersRepository.findByIdAndDelFalse(userId)
                .orElseThrow(() -> new NotFoundException("사용자를 찾을 수 없습니다."));
        UsersInformation info = usersInformationRepository.findByUsersIdAndDelFalse(userId)
                .orElseThrow(() -> new NotFoundException("사용자 정보를 찾을 수 없습니다."));

        String name = textEncryptor.decrypt(info.getNameEnc());
        return new MeResponse(
                user.getId(),
                name,
                info.getProfileImageUrl(),
                user.getRole(),
                user.getStatus());
    }
}
