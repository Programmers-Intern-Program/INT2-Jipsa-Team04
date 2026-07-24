package com.jipsa.config;

import com.jipsa.auth.JwtAuthenticationFilter;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.annotation.Order;
import org.springframework.http.HttpMethod;
import org.springframework.security.config.annotation.method.configuration.EnableMethodSecurity;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;
import org.springframework.web.cors.CorsConfiguration;
import org.springframework.web.cors.CorsConfigurationSource;
import org.springframework.web.cors.UrlBasedCorsConfigurationSource;

import java.util.List;

@Configuration
@EnableWebSecurity
@EnableMethodSecurity
public class SecurityConfig {

    private final JwtAuthenticationFilter jwtAuthenticationFilter;

    public SecurityConfig(JwtAuthenticationFilter jwtAuthenticationFilter) {
        this.jwtAuthenticationFilter = jwtAuthenticationFilter;
    }

    @Bean
    @Order(2)
    public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
        http
            // 프론트(Vite dev server, http://localhost:5173)에서의 브라우저 직접 호출 허용.
            // dev에서는 Vite proxy로도 우회되지만, proxy 없이 8080을 직접 부르는 경우/배포를 위해 둔다.
            .cors(cors -> cors.configurationSource(corsConfigurationSource()))
            // Stateless JWT API: no server sessions, no CSRF tokens, no browser login form.
            .csrf(csrf -> csrf.disable())
            .httpBasic(basic -> basic.disable())
            .formLogin(form -> form.disable())
            .sessionManagement(sm -> sm.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
            .authorizeHttpRequests(auth -> auth
                .requestMatchers("/", "/health").permitAll()   // public paths
                // 구글 로그인은 토큰 발급 전 호출되므로 인증 없이 열어둔다.
                .requestMatchers(HttpMethod.POST, "/api/v1/auth/oauth/google").permitAll()
                // 토큰 재발급은 만료된 Access Token 상태에서 호출되므로 인증 없이 열어둔다.
                .requestMatchers(HttpMethod.POST, "/api/v1/auth/refresh").permitAll()
                // 로그아웃도 만료된 Access Token 상태에서 호출될 수 있으므로 인증 없이 열어둔다.
                .requestMatchers(HttpMethod.POST, "/api/v1/auth/logout").permitAll()
                .anyRequest().authenticated()                  // <-- STRICT default
                // To leave everything open while you have no endpoints yet, swap the
                // two lines above for a single:  .anyRequest().permitAll()
            )
            // Our JWT filter runs before Spring's username/password filter.
            .addFilterBefore(jwtAuthenticationFilter, UsernamePasswordAuthenticationFilter.class);

        return http.build();
        // NOTE: OAuth is intentionally NOT wired here yet. In Wave B this gets a
        // .oauth2Login(oauth -> oauth.successHandler(...)) block once Google creds exist.
    }

    /**
     * CORS 정책. 프론트 개발 서버(http://localhost:5173) origin만 허용한다.
     * 인증은 Authorization: Bearer 헤더(토큰) 방식이라 쿠키를 쓰지 않으므로 allowCredentials는
     * 켜지 않는다. 허용 메서드/헤더는 프론트가 실제로 쓰는 것만 열어둔다.
     */
    @Bean
    public CorsConfigurationSource corsConfigurationSource() {
        CorsConfiguration config = new CorsConfiguration();
        config.setAllowedOrigins(List.of("http://localhost:5173", "https://jipsa.uk"));
        config.setAllowedMethods(List.of("GET", "POST", "PATCH", "DELETE", "OPTIONS"));
        config.setAllowedHeaders(List.of("Authorization", "Content-Type"));
        // role 변경 감지 시 JwtAuthenticationFilter가 실어 보내는 새 Access Token 헤더 —
        // 커스텀 응답 헤더는 노출(expose)하지 않으면 브라우저 JS에서 못 읽는다.
        config.setExposedHeaders(List.of(JwtAuthenticationFilter.NEW_ACCESS_TOKEN_HEADER));

        UrlBasedCorsConfigurationSource source = new UrlBasedCorsConfigurationSource();
        source.registerCorsConfiguration("/**", config);
        return source;
    }
}
