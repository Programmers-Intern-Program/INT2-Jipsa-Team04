package com.jipsa.internal;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.AuthorityUtils;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.List;

public class InternalTokenFilter extends OncePerRequestFilter {

    private static final String HEADER = "X-Internal-Token";

    private final String expectedToken;
    private final List<String> allowedIps;

    public InternalTokenFilter(String expectedToken, List<String> allowedIps) {
        this.expectedToken = expectedToken;
        this.allowedIps = allowedIps;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                    HttpServletResponse response,
                                    FilterChain filterChain) throws ServletException, IOException {
        if (expectedToken == null || expectedToken.isBlank()) {
            reject(response, "internal token not configured");
            return;
        }
        String provided = request.getHeader(HEADER);
        if (provided == null || !constantTimeEquals(provided, expectedToken)) {
            reject(response, "invalid internal token");
            return;
        }
        if (!ipAllowed(request.getRemoteAddr())) {
            reject(response, "ip not allowed");
            return;
        }
        UsernamePasswordAuthenticationToken authentication = new UsernamePasswordAuthenticationToken(
                "rag-service", null, AuthorityUtils.createAuthorityList("ROLE_INTERNAL"));
        SecurityContextHolder.getContext().setAuthentication(authentication);
        filterChain.doFilter(request, response);
    }

    private boolean ipAllowed(String remoteAddr) {
        List<String> ips = allowedIps == null
                ? List.of()
                : allowedIps.stream().filter(s -> !s.isBlank()).toList();
        if (ips.isEmpty()) {
            return true;
        }
        return ips.contains(remoteAddr);
    }

    private void reject(HttpServletResponse response, String reason) throws IOException {
        SecurityContextHolder.clearContext();
        response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
        response.setContentType("application/json");
        response.getWriter().write("{\"error\":\"" + reason + "\"}");
    }

    private static boolean constantTimeEquals(String a, String b) {
        return MessageDigest.isEqual(
                a.getBytes(StandardCharsets.UTF_8),
                b.getBytes(StandardCharsets.UTF_8));
    }
}