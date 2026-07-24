package com.jipsa.auth;

import com.jipsa.user.UsersRepository;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.lang.NonNull;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.web.authentication.WebAuthenticationDetailsSource;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.List;

@Component
public class JwtAuthenticationFilter extends OncePerRequestFilter {

    /**
     * role이 바뀐 뒤 첫 요청에서 서버가 조용히 재발급한 새 Access Token을 실어 보내는 응답 헤더.
     * 브라우저가 커스텀 헤더를 읽으려면 CORS에서 노출(exposeHeaders)해야 한다 — {@code SecurityConfig} 참고.
     */
    public static final String NEW_ACCESS_TOKEN_HEADER = "X-New-Access-Token";

    /**
     * 같은 타이밍에 같이 내려주는 새 Refresh Token 헤더. {@code AdminService.updateRole}이 role
     * 변경 시 대상자의 기존 Refresh Token을 전부 방어적으로 폐기하는데(리뷰 지적: 재발급 없이
     * 폐기만 하면, 이 Access Token마저 자연 만료된 뒤 그 폐기된 Refresh Token으로 재발급을
     * 시도하다 401을 맞아 결국 강제 로그아웃된다 — "재로그인 없이 반영"이라는 목표와 어긋남),
     * 여기서 새 Refresh Token을 같이 발급해 그 문제를 막는다.
     */
    public static final String NEW_REFRESH_TOKEN_HEADER = "X-New-Refresh-Token";

    private final JwtService jwtService;
    private final UserRoleCache userRoleCache;
    private final UsersRepository usersRepository;
    private final RefreshTokenService refreshTokenService;

    public JwtAuthenticationFilter(JwtService jwtService, UserRoleCache userRoleCache,
                                    UsersRepository usersRepository, RefreshTokenService refreshTokenService) {
        this.jwtService = jwtService;
        this.userRoleCache = userRoleCache;
        this.usersRepository = usersRepository;
        this.refreshTokenService = refreshTokenService;
    }

    @Override
    protected void doFilterInternal(@NonNull HttpServletRequest request,
                                    @NonNull HttpServletResponse response,
                                    @NonNull FilterChain filterChain)
            throws ServletException, IOException {

        String header = request.getHeader("Authorization");
        if (header != null && header.startsWith("Bearer ")) {
            String token = header.substring(7);
            jwtService.validateAndGetPrincipal(token).ifPresent(principal -> {
                // 인가는 토큰에 찍힌 role claim이 아니라 지금 이 순간의 DB role(캐시 경유)로 판단한다.
                // 그래야 관리자 권한 회수/부여가 재로그인 없이 바로 다음 요청부터 반영된다 — claim은
                // 발급 시점 값으로 고정돼 있어 그대로 믿으면 권한 변경이 토큰 만료 전까지 반영 안 됨.
                String currentRole = currentRoleFor(principal.userId(), principal.role());
                List<SimpleGrantedAuthority> authorities = currentRole != null
                        ? List.of(new SimpleGrantedAuthority("ROLE_" + currentRole))
                        : List.of();
                var auth = new UsernamePasswordAuthenticationToken(
                        principal.userId(), null, authorities);
                auth.setDetails(new WebAuthenticationDetailsSource().buildDetails(request));
                SecurityContextHolder.getContext().setAuthentication(auth);

                // 캐시(=DB)의 role이 토큰 claim과 다르면 role이 변경된 뒤 첫 요청이라는 뜻 —
                // 새 Access Token과 새 Refresh Token을 함께 만들어 응답 헤더로 내려보내 프론트가
                // 조용히 갈아끼우게 한다. Refresh Token도 같이 재발급해야, 이 Access Token마저
                // 나중에 자연 만료됐을 때 (role 변경 시 방어적으로 폐기된) 옛 Refresh Token으로
                // 갱신을 시도하다 401을 맞아 강제 로그아웃되는 걸 막을 수 있다.
                if (currentRole != null && !currentRole.equals(principal.role())) {
                    String newToken = jwtService.generateToken(principal.userId(), currentRole);
                    response.setHeader(NEW_ACCESS_TOKEN_HEADER, newToken);
                    String newRefreshToken = refreshTokenService.issue(principal.userId());
                    response.setHeader(NEW_REFRESH_TOKEN_HEADER, newRefreshToken);
                }
            });
        }
        filterChain.doFilter(request, response);   // always continue; unauth requests just stay anonymous
    }

    /**
     * 캐시에서 현재 role을 찾고, 미스면 DB에서 한 번 채운다. DB에도 없으면(탈퇴 등으로 사용자
     * 행 자체가 없는 극단적 경우) 이전 동작과 동일하게 토큰 claim을 그대로 신뢰한다 — 이 필터의
     * 책임은 인가이지 계정 상태 검증이 아니고, 계정 상태는 각 서비스 계층에서 별도로 검사한다.
     */
    private String currentRoleFor(Long userId, String tokenRole) {
        return userRoleCache.get(userId)
                .orElseGet(() -> usersRepository.findRoleById(userId)
                        .map(dbRole -> {
                            userRoleCache.put(userId, dbRole);
                            return dbRole;
                        })
                        .orElse(tokenRole));
    }
}
