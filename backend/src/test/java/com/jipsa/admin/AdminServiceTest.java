package com.jipsa.admin;

import com.jipsa.auth.RefreshTokenService;
import com.jipsa.auth.UserRoleCache;
import com.jipsa.user.Users;
import com.jipsa.user.UsersRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.data.domain.PageImpl;
import org.springframework.data.domain.Pageable;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

/**
 * AdminService의 비즈니스 로직만 검증한다. ADMIN 권한 검증은 더 이상 이 서비스의 책임이
 * 아니다 — {@code AdminController}의 {@code @PreAuthorize("hasRole('ADMIN')")}이 인증 계층에서
 * 걸러내므로, 실제 로그인 토큰(ADMIN/USERS)으로 200/403을 확인하는 통합 테스트는
 * {@code AdminAuthorizationIntegrationTest}를 참고.
 */
@ExtendWith(MockitoExtension.class)
class AdminServiceTest {

    private static final Long ADMIN_ID = 1L;
    private static final Long TARGET_ID = 2L;
    // AdminService.SUSPENDABLE_TYPES는 private라 테스트에서 직접 못 써서 값만 그대로 복제.
    // 리포지토리 mock의 인자 매칭용이라 AdminService 쪽 값이 바뀌면 이 상수도 같이 바꿔야 한다.
    private static final Set<SanctionType> SUSPENDABLE_TYPES_FOR_TEST =
            Set.of(SanctionType.TEMP_SUSPEND, SanctionType.PERMANENT_SUSPEND);

    @Mock
    private UsersRepository usersRepository;
    @Mock
    private UserSanctionRepository userSanctionRepository;
    @Mock
    private UserRoleCache userRoleCache;
    @Mock
    private RefreshTokenService refreshTokenService;

    private AdminService adminService;

    @BeforeEach
    void setUp() {
        adminService = new AdminService(usersRepository, userSanctionRepository, userRoleCache, refreshTokenService);
    }

    private Users userWithRole(Long id, String role) {
        Users user = new Users();
        user.setId(id);
        user.setRole(role);
        return user;
    }

    @Test
    void listUsers_문서수와함께_목록을반환한다() {
        AdminUserProjection projection = mockProjection(TARGET_ID, "USERS", "ACTIVE", false, 3L);
        when(usersRepository.findAllWithDocumentCount(any(Pageable.class)))
                .thenReturn(new PageImpl<>(List.of(projection)));

        AdminUserListResponse response = adminService.listUsers(ADMIN_ID, 0, 20);

        assertThat(response.total()).isEqualTo(1);
        assertThat(response.items().get(0).userId()).isEqualTo(TARGET_ID);
        assertThat(response.items().get(0).documentCount()).isEqualTo(3L);
        assertThat(response.items().get(0).lastLoginAt()).isNull(); // Refresh_Tokens 미구현 — 별도 이슈
    }

    @Test
    void suspend_자기자신을대상으로하면_400() {
        SuspendUserRequest request = new SuspendUserRequest("TEMP_SUSPEND", "약관 위반", null);

        assertThatThrownBy(() -> adminService.suspend(ADMIN_ID, ADMIN_ID, request))
                .isInstanceOf(SelfTargetNotAllowedException.class);
    }

    @Test
    void suspend_대상사용자가없으면_404() {
        when(usersRepository.findById(TARGET_ID)).thenReturn(Optional.empty());

        SuspendUserRequest request = new SuspendUserRequest("TEMP_SUSPEND", "약관 위반", null);

        assertThatThrownBy(() -> adminService.suspend(ADMIN_ID, TARGET_ID, request))
                .isInstanceOf(AdminUserNotFoundException.class);
    }

    @Test
    void suspend_정상요청이면_제재이력을생성하고_상태를변경한다() {
        Users target = userWithRole(TARGET_ID, "USERS");
        target.setStatus("ACTIVE");
        when(usersRepository.findById(TARGET_ID)).thenReturn(Optional.of(target));

        SuspendUserRequest request = new SuspendUserRequest("TEMP_SUSPEND", "약관 위반 신고 누적", null);
        adminService.suspend(ADMIN_ID, TARGET_ID, request);

        ArgumentCaptor<UserSanction> captor = ArgumentCaptor.forClass(UserSanction.class);
        verify(userSanctionRepository).save(captor.capture());
        UserSanction saved = captor.getValue();
        assertThat(saved.getUsersId()).isEqualTo(TARGET_ID);
        assertThat(saved.getSanctionedByUsersId()).isEqualTo(ADMIN_ID);
        assertThat(saved.getSanctionType()).isEqualTo(SanctionType.TEMP_SUSPEND);
        assertThat(saved.getRestoreUserStatus()).isEqualTo("ACTIVE");
        assertThat(target.getStatus()).isEqualTo("SUSPENDED");
    }

    @Test
    void suspend_유효하지않은sanctionType이면_400() {
        when(usersRepository.findById(TARGET_ID)).thenReturn(Optional.of(userWithRole(TARGET_ID, "USERS")));

        SuspendUserRequest request = new SuspendUserRequest("NOT_A_TYPE", "사유", null);

        assertThatThrownBy(() -> adminService.suspend(ADMIN_ID, TARGET_ID, request))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void suspend_계정정지가아닌sanctionType이면_400() {
        // WARNING은 SanctionType엔 있지만 "계정 정지"를 뜻하지 않아 suspend 엔드포인트에서 거부해야 함
        when(usersRepository.findById(TARGET_ID)).thenReturn(Optional.of(userWithRole(TARGET_ID, "USERS")));

        SuspendUserRequest request = new SuspendUserRequest("WARNING", "사유", null);

        assertThatThrownBy(() -> adminService.suspend(ADMIN_ID, TARGET_ID, request))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void suspend_이미정지된사용자면_409() {
        Users target = userWithRole(TARGET_ID, "USERS");
        target.setStatus("SUSPENDED");
        when(usersRepository.findById(TARGET_ID)).thenReturn(Optional.of(target));

        SuspendUserRequest request = new SuspendUserRequest("TEMP_SUSPEND", "사유", null);

        assertThatThrownBy(() -> adminService.suspend(ADMIN_ID, TARGET_ID, request))
                .isInstanceOf(AdminActionConflictException.class);
    }

    @Test
    void suspend_이미삭제된사용자면_409() {
        Users target = userWithRole(TARGET_ID, "USERS");
        target.setDel(true);
        when(usersRepository.findById(TARGET_ID)).thenReturn(Optional.of(target));

        SuspendUserRequest request = new SuspendUserRequest("TEMP_SUSPEND", "사유", null);

        assertThatThrownBy(() -> adminService.suspend(ADMIN_ID, TARGET_ID, request))
                .isInstanceOf(AdminActionConflictException.class);
    }

    @Test
    void unsuspend_활성제재이력이없으면_404() {
        when(usersRepository.findById(TARGET_ID)).thenReturn(Optional.of(userWithRole(TARGET_ID, "USERS")));
        when(userSanctionRepository.findFirstByUsersIdAndSanctionTypeInAndSanctionStatusOrderByStartedAtDesc(
                        TARGET_ID, SUSPENDABLE_TYPES_FOR_TEST, SanctionStatus.ACTIVE))
                .thenReturn(Optional.empty());

        assertThatThrownBy(() -> adminService.unsuspend(ADMIN_ID, TARGET_ID, new UnsuspendUserRequest("오제재 확인됨")))
                .isInstanceOf(NoActiveSanctionException.class);
    }

    @Test
    void unsuspend_이미삭제된사용자면_409() {
        Users target = userWithRole(TARGET_ID, "USERS");
        target.setDel(true);
        when(usersRepository.findById(TARGET_ID)).thenReturn(Optional.of(target));

        assertThatThrownBy(() -> adminService.unsuspend(ADMIN_ID, TARGET_ID, new UnsuspendUserRequest("사유")))
                .isInstanceOf(AdminActionConflictException.class);
    }

    @Test
    void unsuspend_정상요청이면_제재를해제하고_상태를복원한다() {
        Users target = userWithRole(TARGET_ID, "USERS");
        target.setStatus("SUSPENDED");
        when(usersRepository.findById(TARGET_ID)).thenReturn(Optional.of(target));

        UserSanction activeSanction = new UserSanction(
                TARGET_ID, ADMIN_ID, SanctionType.TEMP_SUSPEND, "약관 위반", "ACTIVE", null);
        when(userSanctionRepository.findFirstByUsersIdAndSanctionTypeInAndSanctionStatusOrderByStartedAtDesc(
                        TARGET_ID, SUSPENDABLE_TYPES_FOR_TEST, SanctionStatus.ACTIVE))
                .thenReturn(Optional.of(activeSanction));

        adminService.unsuspend(ADMIN_ID, TARGET_ID, new UnsuspendUserRequest("오제재 확인됨"));

        assertThat(activeSanction.getSanctionStatus()).isEqualTo(SanctionStatus.LIFTED);
        assertThat(activeSanction.getLiftedByUsersId()).isEqualTo(ADMIN_ID);
        assertThat(activeSanction.getLiftReason()).isEqualTo("오제재 확인됨");
        assertThat(target.getStatus()).isEqualTo("ACTIVE");
    }

    @Test
    void delete_정상요청이면_소프트삭제하고_제재이력을남긴다() {
        Users target = userWithRole(TARGET_ID, "USERS");
        when(usersRepository.findById(TARGET_ID)).thenReturn(Optional.of(target));

        adminService.delete(ADMIN_ID, TARGET_ID, new DeleteUserRequest("악성 사용자 신고 누적"));

        assertThat(target.isDel()).isTrue();
        assertThat(target.getStatus()).isEqualTo("WITHDRAWN");
        ArgumentCaptor<UserSanction> captor = ArgumentCaptor.forClass(UserSanction.class);
        verify(userSanctionRepository).save(captor.capture());
        assertThat(captor.getValue().getSanctionType()).isEqualTo(SanctionType.ACCOUNT_DELETE);
    }

    @Test
    void delete_이미삭제된사용자면_409() {
        Users target = userWithRole(TARGET_ID, "USERS");
        target.setDel(true);
        when(usersRepository.findById(TARGET_ID)).thenReturn(Optional.of(target));

        assertThatThrownBy(() -> adminService.delete(ADMIN_ID, TARGET_ID, new DeleteUserRequest("사유")))
                .isInstanceOf(AdminActionConflictException.class);
    }

    @Test
    void updateRole_유효하지않은값이면_400() {
        when(usersRepository.findById(TARGET_ID)).thenReturn(Optional.of(userWithRole(TARGET_ID, "USERS")));

        assertThatThrownBy(() -> adminService.updateRole(ADMIN_ID, TARGET_ID, new UpdateRoleRequest("USER")))
                .isInstanceOf(IllegalArgumentException.class);

        verifyNoInteractions(userRoleCache, refreshTokenService);   // 검증 실패 시 캐시/토큰엔 손대지 않는다
    }

    @Test
    void updateRole_정상요청이면_role을변경한다() {
        Users target = userWithRole(TARGET_ID, "USERS");
        when(usersRepository.findById(TARGET_ID)).thenReturn(Optional.of(target));

        adminService.updateRole(ADMIN_ID, TARGET_ID, new UpdateRoleRequest("ADMIN"));

        assertThat(target.getRole()).isEqualTo("ADMIN");
    }

    @Test
    void updateRole_정상요청이면_캐시를갱신하고_대상자의_refresh_token을_전부폐기한다() {
        // 재로그인 없이 반영: 필터가 다음 요청부터 바로 새 role을 보게 캐시를 갱신하고,
        // 재검증 로직이 뚫리는 경우를 대비해 기존 refresh token도 방어적으로 폐기한다.
        Users target = userWithRole(TARGET_ID, "USERS");
        when(usersRepository.findById(TARGET_ID)).thenReturn(Optional.of(target));

        adminService.updateRole(ADMIN_ID, TARGET_ID, new UpdateRoleRequest("ADMIN"));

        verify(userRoleCache).put(TARGET_ID, "ADMIN");
        verify(refreshTokenService).revokeAllForUser(TARGET_ID, RefreshTokenService.REVOKED_REASON_ROLE_CHANGED);
    }

    @Test
    void updateRole_자기자신이면_400() {
        assertThatThrownBy(() -> adminService.updateRole(ADMIN_ID, ADMIN_ID, new UpdateRoleRequest("USERS")))
                .isInstanceOf(SelfTargetNotAllowedException.class);
    }

    private AdminUserProjection mockProjection(Long userId, String role, String status, boolean del, Long documentCount) {
        AdminUserProjection projection = org.mockito.Mockito.mock(AdminUserProjection.class);
        when(projection.getUserId()).thenReturn(userId);
        when(projection.getRole()).thenReturn(role);
        when(projection.getStatus()).thenReturn(status);
        when(projection.getDel()).thenReturn(del);
        when(projection.getCreatedAt()).thenReturn(LocalDateTime.now());
        when(projection.getDocumentCount()).thenReturn(documentCount);
        return projection;
    }
}
