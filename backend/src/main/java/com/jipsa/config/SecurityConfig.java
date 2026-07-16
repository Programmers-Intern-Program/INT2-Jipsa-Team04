package com.jipsa.config;

import com.jipsa.auth.JwtAuthenticationFilter;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpMethod;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;

@Configuration
@EnableWebSecurity
public class SecurityConfig {

    private final JwtAuthenticationFilter jwtAuthenticationFilter;

    public SecurityConfig(JwtAuthenticationFilter jwtAuthenticationFilter) {
        this.jwtAuthenticationFilter = jwtAuthenticationFilter;
    }

    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
        http
            // Stateless JWT API: no server sessions, no CSRF tokens, no browser login form.
            .csrf(csrf -> csrf.disable())
            .httpBasic(basic -> basic.disable())
            .formLogin(form -> form.disable())
            .sessionManagement(sm -> sm.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
            .authorizeHttpRequests(auth -> auth
                .requestMatchers("/", "/health").permitAll()   // public paths
                // 구글 로그인은 토큰 발급 전 호출되므로 인증 없이 열어둔다.
                .requestMatchers(HttpMethod.POST, "/api/v1/auth/oauth/google").permitAll()
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
}
