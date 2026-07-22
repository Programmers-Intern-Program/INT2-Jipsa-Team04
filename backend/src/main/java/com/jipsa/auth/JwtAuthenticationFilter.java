package com.jipsa.auth;

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

    private final JwtService jwtService;

    public JwtAuthenticationFilter(JwtService jwtService) {
        this.jwtService = jwtService;
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
                // Principal = the user's id. Authority comes from the role claim baked into the
                // token at issue time (ADMIN → ROLE_ADMIN, USERS → ROLE_USERS). If the token has
                // no role claim (e.g. issued before this change), grant no role authority —
                // fail closed rather than guessing.
                List<SimpleGrantedAuthority> authorities = principal.role() != null
                        ? List.of(new SimpleGrantedAuthority("ROLE_" + principal.role()))
                        : List.of();
                var auth = new UsernamePasswordAuthenticationToken(
                        principal.userId(), null, authorities);
                auth.setDetails(new WebAuthenticationDetailsSource().buildDetails(request));
                SecurityContextHolder.getContext().setAuthentication(auth);
            });
        }
        filterChain.doFilter(request, response);   // always continue; unauth requests just stay anonymous
    }
}
