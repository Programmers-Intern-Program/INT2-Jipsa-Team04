package com.jipsa.metadata;

import com.anthropic.client.AnthropicClient;
import com.anthropic.models.messages.ContentBlock;
import com.anthropic.models.messages.Message;
import com.anthropic.models.messages.MessageCreateParams;
import com.anthropic.models.messages.TextBlock;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.util.stream.Collectors;

@Component
public class AutoMetadataClient {

    private static final String SYSTEM_PROMPT = """
            문서 앞부분 일부를 보고 메타데이터를 추출한다. 설명 없이 JSON 객체 하나만 출력한다.
            {"summary":"2문장 이하 한국어 요약","keywords":["최대 5개"],"entities":["날짜·인물·금액·조직 등 최대 5개"],"documentType":"아래 목록 중 하나 또는 null","confidence":0.0}
            documentType 후보: 계약서, 보고서, 청구서, 회의록, 이력서, 제안서, 견적서, 영수증, 발표자료, 공문, 논문, 기타
            근거가 부족하면 documentType은 null, confidence는 낮게 준다.
            """;

    private final AnthropicClient anthropicClient;
    private final ObjectMapper objectMapper;
    private final String model;
    private final long maxTokens;

    public AutoMetadataClient(AnthropicClient anthropicClient,
                              ObjectMapper objectMapper,
                              @Value("${app.metadata.ai.model:claude-haiku-4-5-20251001}") String model,
                              @Value("${app.metadata.ai.max-tokens:400}") long maxTokens) {
        this.anthropicClient = anthropicClient;
        this.objectMapper = objectMapper;
        this.model = model;
        this.maxTokens = maxTokens;
    }

    public AutoMetadataResult generate(String documentSample) {
        MessageCreateParams params = MessageCreateParams.builder()
                .model(model)
                .maxTokens(maxTokens)
                .system(SYSTEM_PROMPT)
                .addUserMessage(documentSample)
                .build();

        Message message = anthropicClient.messages().create(params);
        String responseText = extractText(message);
        try {
            return objectMapper.readValue(stripCodeFence(responseText), AutoMetadataResult.class);
        } catch (JsonProcessingException e) {
            throw new AutoMetadataParseException("AI 메타데이터 JSON 파싱에 실패했습니다.", e);
        }
    }

    private String extractText(Message message) {
        return message.content().stream()
                .filter(ContentBlock::isText)
                .map(ContentBlock::asText)
                .map(TextBlock::text)
                .collect(Collectors.joining());
    }

    private String stripCodeFence(String text) {
        String trimmed = text.trim();
        if (trimmed.startsWith("```")) {
            trimmed = trimmed.replaceFirst("^```(?:json)?\\s*", "");
            trimmed = trimmed.replaceFirst("```\\s*$", "");
        }
        return trimmed.trim();
    }
}