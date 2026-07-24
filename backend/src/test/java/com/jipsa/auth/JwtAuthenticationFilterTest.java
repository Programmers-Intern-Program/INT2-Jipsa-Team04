package com.jipsa.auth;

import com.jipsa.user.UsersRepository;
import jakarta.servlet.FilterChain;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * JwtAuthenticationFilter의 인가 재검증 로직을 검증한다.
 *
 * <p>핵심 규칙: 인가는 토큰의 role claim이 아니라 {@link UserRoleCache}(미스 시 DB)의
 * <b>지금 이 순간</b> role로 판단한다 — 관리자 권한 변경(부여/회수)이 재로그인 없이 다음
 * 요청부터 바로 반영되게 하기 위함이다. claim과 현재 role이 다르면 새 Access Token을
 * {@link JwtAuthenticationFilter#NEW_ACCESS_TOKEN_HEADER} 응답 헤더로 내려준다.
 *
 * <p>실제 HTTP 배선(PreAuthorize와 맞물린 동작)은 {@code AdminAuthorizationIntegrationTest}가
 * 별도로 검증하고, 여기서는 필터 단독 로직만 본다. {@code doFilterInternal}은 protected지만
 * 같은 패키지 테스트라 직접 호출한다(OncePerRequestFilter의 중복 실행 방지 로직은 관심사가 아님).
 */
@ExtendWith(MockitoExtension.class)
class JwtAuthenticationFilterTest {

    private static final Long USER_ID = 1L;
    // HS256 최소 256비트(32바이트) 요구를 넉넉히 넘기는 테스트 전용 키.
    private static final String TEST_SECRET = "test-secret-key-at-least-32-bytes-long-padding-1234567890";

    @Mock
    private UsersRepository usersRepository;

    private UserRoleCache userRoleCache;
    private JwtService jwtService;
    private JwtAuthenticationFilter filter;

    @BeforeEach
    void setUp() {
        userRoleCache = new UserRoleCache();
        jwtService = new JwtService(TEST_SECRET, 2_700_000L);
        filter = new JwtAuthenticationFilter(jwtService, userRoleCache, usersRepository);
    }

    @AfterEach
    void clearContext() {
        SecurityContextHolder.clearContext();
    }

    private MockHttpServletRequest requestWithToken(String token) {
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.addHeader("Authorization", "Bearer " + token);
        return request;
    }

    @Test
    void 캐시의_role이_토큰과_같으면_그대로_인증하고_새토큰헤더를_내려주지_않는다() throws Exception {
        userRoleCache.put(USER_ID, "USERS");
        String token = jwtService.generateToken(USER_ID, "USERS");
        MockHttpServletRequest request = requestWithToken(token);
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        filter.doFilterInternal(request, response, chain);

        Authentication auth = SecurityContextHolder.getContext().getAuthentication();
        assertThat(auth.getAuthorities()).extracting(Object::toString).containsExactly("ROLE_USERS");
        assertThat(response.getHeader(JwtAuthenticationFilter.NEW_ACCESS_TOKEN_HEADER)).isNull();
        verify(chain).doFilter(request, response);
    }

    @Test
    void 캐시의_role이_토큰과_다르면_현재role로_인가하고_새토큰을_헤더로_내려준다() throws Exception {
        // 승격 시나리오: 토큰은 USERS로 발급됐지만 그 사이 관리자가 ADMIN으로 바꿨다.
        userRoleCache.put(USER_ID, "ADMIN");
        String token = jwtService.generateToken(USER_ID, "USERS");
        MockHttpServletRequest request = requestWithToken(token);
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        filter.doFilterInternal(request, response, chain);

        Authentication auth = SecurityContextHolder.getContext().getAuthentication();
        assertThat(auth.getAuthorities()).extracting(Object::toString).containsExactly("ROLE_ADMIN");

        String newToken = response.getHeader(JwtAuthenticationFilter.NEW_ACCESS_TOKEN_HEADER);
        assertThat(newToken).isNotBlank();
        Optional<JwtPrincipal> newPrincipal = jwtService.validateAndGetPrincipal(newToken);
        assertThat(newPrincipal).isPresent();
        assertThat(newPrincipal.get().userId()).isEqualTo(USER_ID);
        assertThat(newPrincipal.get().role()).isEqualTo("ADMIN");
    }

    @Test
    void 캐시미스면_DB에서_채우고_그값으로_인가한다() throws Exception {
        String token = jwtService.generateToken(USER_ID, "USERS");
        when(usersRepository.findRoleById(USER_ID)).thenReturn(Optional.of("ADMIN"));
        MockHttpServletRequest request = requestWithToken(token);
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        filter.doFilterInternal(request, response, chain);

        Authentication auth = SecurityContextHolder.getContext().getAuthentication();
        assertThat(auth.getAuthorities()).extracting(Object::toString).containsExactly("ROLE_ADMIN");
        // 다음 요청부터는 DB를 다시 안 타도록 캐시가 채워져 있어야 한다.
        assertThat(userRoleCache.get(USER_ID)).contains("ADMIN");
    }

    @Test
    void 캐시와_DB_모두_미스면_토큰의_role을_그대로_신뢰한다() throws Exception {
        // 탈퇴 등으로 DB에 사용자 행 자체가 없는 극단적인 경우 — 이 필터의 책임은 인가이지
        // 계정 상태 검증이 아니므로, 이전 동작(토큰 claim 신뢰)을 그대로 유지한다.
        String token = jwtService.generateToken(USER_ID, "USERS");
        when(usersRepository.findRoleById(USER_ID)).thenReturn(Optional.empty());
        MockHttpServletRequest request = requestWithToken(token);
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        filter.doFilterInternal(request, response, chain);

        Authentication auth = SecurityContextHolder.getContext().getAuthentication();
        assertThat(auth.getAuthorities()).extracting(Object::toString).containsExactly("ROLE_USERS");
        assertThat(response.getHeader(JwtAuthenticationFilter.NEW_ACCESS_TOKEN_HEADER)).isNull();
    }

    @Test
    void 토큰이_없으면_인증하지_않고_체인만_진행한다() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest();
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        filter.doFilterInternal(request, response, chain);

        assertThat(SecurityContextHolder.getContext().getAuthentication()).isNull();
        verify(chain).doFilter(request, response);
    }
}
