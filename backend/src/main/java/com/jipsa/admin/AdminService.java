package com.jipsa.admin;

import com.jipsa.auth.RefreshTokenService;
import com.jipsa.auth.UserRoleCache;
import com.jipsa.user.Users;
import com.jipsa.user.UsersRepository;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Set;

/**
 * 관리자 전용 사용자 관리 (Req.5~12). ADMIN 권한 검증은 {@code AdminController}의
 * {@code @PreAuthorize("hasRole('ADMIN')")}에서 인증 계층으로 처리하고, 여기서는 매 요청마다
 * 다시 검증하지 않는다. actingAdminId는 자기 자신 대상 조작 차단(requireNotSelf)과
 * 감사(누가 처리했는지) 기록용으로만 쓰인다.
 */
@Service
public class AdminService {

    private static final int DEFAULT_PAGE_SIZE = 20;
    private static final Set<String> VALID_ROLES = Set.of("ADMIN", "USERS");
    private static final String DEFAULT_RESTORE_STATUS = "ACTIVE";
    // suspend 엔드포인트에서 실제로 계정을 잠그는(Status=SUSPENDED) 의미를 가지는 타입만 받는다.
    // WARNING/UPLOAD_LIMIT/LOGIN_BLOCK은 Sanction_Type엔 있어도 "계정 정지"를 뜻하지 않아 제외.
    private static final Set<SanctionType> SUSPENDABLE_TYPES =
            Set.of(SanctionType.TEMP_SUSPEND, SanctionType.PERMANENT_SUSPEND);

    private final UsersRepository usersRepository;
    private final UserSanctionRepository userSanctionRepository;
    private final UserRoleCache userRoleCache;
    private final RefreshTokenService refreshTokenService;

    public AdminService(UsersRepository usersRepository, UserSanctionRepository userSanctionRepository,
                         UserRoleCache userRoleCache, RefreshTokenService refreshTokenService) {
        this.usersRepository = usersRepository;
        this.userSanctionRepository = userSanctionRepository;
        this.userRoleCache = userRoleCache;
        this.refreshTokenService = refreshTokenService;
    }

    /** GET /api/v1/admin/users — 전체 사용자 목록(가입일/상태/문서수, 최근로그인은 별도 이슈 전까지 null). */
    @Transactional(readOnly = true)
    public AdminUserListResponse listUsers(Long actingAdminId, Integer page, Integer size) {
        int pageNumber = (page != null && page > 0) ? page : 0;
        int pageSize = (size != null && size > 0) ? size : DEFAULT_PAGE_SIZE;
        // 정렬은 UsersRepository.findAllWithDocumentCount() JPQL 안에 order by u.createdAt desc로
        // 고정돼 있어 여기서는 Sort 없는(페이지/사이즈만) Pageable을 넘긴다.
        Pageable pageable = PageRequest.of(pageNumber, pageSize);

        Page<AdminUserProjection> result = usersRepository.findAllWithDocumentCount(pageable);
        List<AdminUserListItem> items = result.getContent().stream()
                .map(this::toListItem)
                .toList();
        return new AdminUserListResponse(items, result.getTotalElements());
    }

    /** POST /api/v1/admin/users/{id}/suspend — User_Sanctions 행 생성 + Users.Status를 SUSPENDED로. */
    @Transactional
    public void suspend(Long actingAdminId, Long targetUserId, SuspendUserRequest request) {
        requireNotSelf(actingAdminId, targetUserId);
        Users target = requireUser(targetUserId);

        // 이미 정지/삭제된 사용자를 또 정지시키면 restoreUserStatus가 "SUSPENDED"로 찍혀서
        // 나중에 해제해도 ACTIVE로 안 돌아가고, 이전 ACTIVE 제재 이력도 영영 안 풀리는
        // 문제가 있어 미리 막는다.
        if (target.isDel()) {
            throw new AdminActionConflictException("이미 삭제된 사용자입니다: " + targetUserId);
        }
        if ("SUSPENDED".equals(target.getStatus())) {
            throw new AdminActionConflictException("이미 정지된 사용자입니다: " + targetUserId);
        }

        SanctionType sanctionType = parseSuspendSanctionType(request.sanctionType());
        String restoreStatus = target.getStatus(); // 정지 직전 상태를 해제 시 복원용으로 스냅샷

        UserSanction sanction = new UserSanction(
                targetUserId, actingAdminId, sanctionType,
                requireNonBlank(request.reason(), "reason"), restoreStatus, request.expiresAt());
        userSanctionRepository.save(sanction);

        target.setStatus("SUSPENDED");
    }

    /** POST /api/v1/admin/users/{id}/unsuspend — 가장 최근 ACTIVE 제재를 LIFTED로 전환하고 상태를 복원. */
    @Transactional
    public void unsuspend(Long actingAdminId, Long targetUserId, UnsuspendUserRequest request) {
        requireNotSelf(actingAdminId, targetUserId);
        Users target = requireUser(targetUserId);

        // 삭제된 사용자는 정지 해제 대상이 아니다 — 삭제(ACCOUNT_DELETE)도 Sanction_Status가
        // 기본 ACTIVE라서, 이 가드가 없으면 아래 조회에서 SUSPENDABLE_TYPES로 걸러지긴 하지만
        // 애초에 "삭제된 계정을 해제한다"는 요청 자체가 의미가 없어 명시적으로 막는다.
        if (target.isDel()) {
            throw new AdminActionConflictException("이미 삭제된 사용자입니다: " + targetUserId);
        }

        UserSanction sanction = userSanctionRepository
                .findFirstByUsersIdAndSanctionTypeInAndSanctionStatusOrderByStartedAtDesc(
                        targetUserId, SUSPENDABLE_TYPES, SanctionStatus.ACTIVE)
                .orElseThrow(() -> new NoActiveSanctionException(targetUserId));

        sanction.setSanctionStatus(SanctionStatus.LIFTED);
        sanction.setLiftedByUsersId(actingAdminId);
        sanction.setLiftedAt(LocalDateTime.now());
        sanction.setLiftReason(requireNonBlank(request.liftedReason(), "liftedReason"));

        String restoreTo = sanction.getRestoreUserStatus() != null
                ? sanction.getRestoreUserStatus()
                : DEFAULT_RESTORE_STATUS;
        target.setStatus(restoreTo);
    }

    /** DELETE /api/v1/admin/users/{id} — 소프트 삭제(Del=true) + 제재 이력(ACCOUNT_DELETE) 기록. */
    @Transactional
    public void delete(Long actingAdminId, Long targetUserId, DeleteUserRequest request) {
        requireNotSelf(actingAdminId, targetUserId);
        Users target = requireUser(targetUserId);

        if (target.isDel()) {
            throw new AdminActionConflictException("이미 삭제된 사용자입니다: " + targetUserId);
        }

        UserSanction sanction = new UserSanction(
                targetUserId, actingAdminId, SanctionType.ACCOUNT_DELETE,
                requireNonBlank(request.reason(), "reason"), null, null);
        userSanctionRepository.save(sanction);

        target.setDel(true);
        target.setStatus("WITHDRAWN");
    }

    /** GET /api/v1/admin/users/{id}/sanctions — 특정 사용자 제재 이력 전체(최신순). */
    @Transactional(readOnly = true)
    public SanctionListResponse getSanctions(Long actingAdminId, Long targetUserId) {
        requireUser(targetUserId); // 대상 사용자 존재 검증

        List<SanctionItem> items = userSanctionRepository.findByUsersIdOrderByStartedAtDesc(targetUserId).stream()
                .map(this::toSanctionItem)
                .toList();
        return new SanctionListResponse(items);
    }

    /**
     * PATCH /api/v1/admin/users/{id}/role — 관리자 권한 부여/해제.
     *
     * <p>DB의 role을 바꾼 뒤 {@link UserRoleCache}도 같은 값으로 갱신한다 — 그래야 대상 사용자의
     * 다음 요청에서 {@code JwtAuthenticationFilter}가 DB를 다시 조회하지 않고도 바로 새 role로
     * 인가를 판단하고, 필요하면 새 Access Token을 응답 헤더로 내려준다(재로그인 불필요).
     *
     * <p>대상 사용자의 활성 Refresh Token도 방어적으로 전부 폐기한다. 위 재검증 로직만으로 이미
     * 권한 변경은 안전하게 반영되므로 필수는 아니지만, 그 로직에 문제가 생기는 경우를 대비한
     * 이중 안전장치다({@link RefreshTokenService#revokeAllForUser}).
     */
    @Transactional
    public void updateRole(Long actingAdminId, Long targetUserId, UpdateRoleRequest request) {
        requireNotSelf(actingAdminId, targetUserId);
        Users target = requireUser(targetUserId);

        String role = request.role();
        if (role == null || !VALID_ROLES.contains(role)) {
            throw new IllegalArgumentException("role은 ADMIN 또는 USERS만 가능합니다: " + role);
        }
        target.setRole(role);
        userRoleCache.put(targetUserId, role);
        refreshTokenService.revokeAllForUser(targetUserId, RefreshTokenService.REVOKED_REASON_ROLE_CHANGED);
    }

    /** 자기 자신을 대상으로 한 정지/해제/삭제/권한변경 차단. */
    private void requireNotSelf(Long actingAdminId, Long targetUserId) {
        if (actingAdminId.equals(targetUserId)) {
            throw new SelfTargetNotAllowedException();
        }
    }

    private Users requireUser(Long userId) {
        return usersRepository.findById(userId)
                .orElseThrow(() -> new AdminUserNotFoundException(userId));
    }

    private SanctionType parseSanctionType(String value) {
        try {
            return SanctionType.valueOf(value);
        } catch (IllegalArgumentException | NullPointerException e) {
            throw new IllegalArgumentException("유효하지 않은 sanctionType입니다: " + value);
        }
    }

    /** suspend 엔드포인트 전용: SUSPENDABLE_TYPES(TEMP_SUSPEND/PERMANENT_SUSPEND)만 허용. */
    private SanctionType parseSuspendSanctionType(String value) {
        SanctionType type = parseSanctionType(value);
        if (!SUSPENDABLE_TYPES.contains(type)) {
            throw new IllegalArgumentException(
                    "suspend 엔드포인트에서는 TEMP_SUSPEND 또는 PERMANENT_SUSPEND만 사용할 수 있습니다: " + value);
        }
        return type;
    }

    private String requireNonBlank(String value, String fieldName) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(fieldName + "은 비어 있을 수 없습니다");
        }
        return value.trim();
    }

    private AdminUserListItem toListItem(AdminUserProjection projection) {
        return new AdminUserListItem(
                projection.getUserId(),
                projection.getRole(),
                projection.getStatus(),
                projection.getDel(),
                projection.getCreatedAt(),
                projection.getDocumentCount(),
                null); // 최근 로그인: Refresh_Tokens 엔티티 추가 전까지 null (별도 이슈)
    }

    private SanctionItem toSanctionItem(UserSanction sanction) {
        return new SanctionItem(
                sanction.getSanctionType().name(),
                sanction.getSanctionStatus().name(),
                sanction.getReason(),
                sanction.getRestoreUserStatus(),
                sanction.getStartedAt(),
                sanction.getExpiresAt(),
                sanction.getLiftedByUsersId(),
                sanction.getLiftedAt(),
                sanction.getLiftReason());
    }
}
