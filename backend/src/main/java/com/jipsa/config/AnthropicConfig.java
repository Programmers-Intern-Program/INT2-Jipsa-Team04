package com.jipsa.config;

import com.anthropic.client.AnthropicClient;
import com.anthropic.client.okhttp.AnthropicOkHttpClient;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class AnthropicConfig {

    /**
     * ANTHROPIC_API_KEY 환경변수를 자동으로 읽는다.
     * 키가 없어도 앱 부팅 자체는 실패하지 않고, 실제로 메시지를 생성하려는 시점에만 실패한다 —
     * 그래서 이 빈을 추가해도 CI/로컬에서 ANTHROPIC_API_KEY 없이 돌리는 다른 테스트들에 영향 없다.
     */
    @Bean
    public AnthropicClient anthropicClient() {
        return AnthropicOkHttpClient.fromEnv();
    }

    /**
     * AnthropicOrganizeClient가 AI 응답 JSON을 OrganizeProposal로 파싱하는 데 쓰는 전용 ObjectMapper.
     * 이 프로젝트의 Spring Boot 4 Jackson 자동 설정은 tools.jackson(Jackson 3) 기반이라
     * com.fasterxml.jackson.databind.ObjectMapper 빈을 자동으로 만들어주지 않는다 — 그래서 직접 등록.
     * 앱 전체의 HTTP 메시지 컨버터(Jackson 3)에는 영향 없다.
     */
    @Bean
    public ObjectMapper objectMapper() {
        return new ObjectMapper();
    }
}
