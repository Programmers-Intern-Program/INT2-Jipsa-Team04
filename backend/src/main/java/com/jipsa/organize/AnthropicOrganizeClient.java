package com.jipsa.organize;

import com.anthropic.client.AnthropicClient;
import com.anthropic.models.messages.ContentBlock;
import com.anthropic.models.messages.Message;
import com.anthropic.models.messages.MessageCreateParams;
import com.anthropic.models.messages.Model;
import com.anthropic.models.messages.TextBlock;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.stream.Collectors;

/**
 * AiOrganizeClient의 실제 구현체 — 현재 폴더 트리 + 파일 목록을 프롬프트에 담아 Claude에게
 * 폴더 재편을 요청하고, 응답 텍스트를 OrganizeProposal JSON으로 파싱한다.
 *
 * tool use(function calling)로 스키마를 강제하는 대신, 시스템 프롬프트로 JSON 스펙을 지정하고
 * 텍스트 응답을 직접 파싱하는 방식을 택했다 — 구현이 단순하고, 파싱된 값에 대한 검증은
 * 어차피 OrganizeService.validate가 담당하므로(존재하지 않는 id, 순환 참조 등) 여기서
 * 스키마 강제까지 이중으로 할 필요는 없다고 판단.
 */
@Component
public class AnthropicOrganizeClient implements AiOrganizeClient {

    private static final long MAX_TOKENS = 8192L;

    private static final String SYSTEM_PROMPT = """
            당신은 사용자의 문서 폴더를 정리해주는 AI입니다.
            주어진 "현재 폴더 트리"와 "파일 목록"을 보고, 파일들을 의미 있는 구조로 재편하는 제안을
            아래 JSON 스키마로만 응답하세요. 설명, 인사말, 마크다운 코드블록 없이 JSON 객체 하나만 출력하세요.

            {
              "newFolders": [
                {
                  "tempId": "이 응답 안에서만 유효한 임시 문자열 id",
                  "name": "새로 만들 폴더 이름",
                  "parentTempId": "부모가 이 응답의 다른 newFolders 항목이면 그 tempId, 아니면 null",
                  "parentFolderId": "부모가 기존 폴더면 그 실제 id(숫자), 아니면 null"
                }
              ],
              "mappings": [
                {
                  "fileId": 파일의 실제 id(숫자),
                  "targetFolderId": "기존 폴더로 옮길 경우 그 실제 id(숫자), 아니면 null",
                  "targetTempId": "새로 만든 폴더로 옮길 경우 그 tempId, 아니면 null",
                  "newName": "파일명을 바꾸고 싶으면 새 이름, 아니면 null"
                }
              ]
            }

            규칙:
            - 새 폴더(newFolders) 각 항목은 parentTempId/parentFolderId 중 정확히 하나만 채우거나 둘 다 null(루트)로 두세요.
            - 파일 매핑(mappings) 각 항목은 targetFolderId/targetTempId 중 정확히 하나만 채우거나 둘 다 null(루트로 이동)로 두세요.
            - targetTempId와 parentTempId는 이번 응답의 newFolders에 실제로 있는 tempId만 참조하세요.
            - fileId, parentFolderId, targetFolderId는 입력으로 주어진 값만 사용하고, 존재하지 않는 값을 지어내지 마세요.
            - 이미 적절한 위치와 이름을 가진 파일은 mappings에 아예 포함하지 않아도 됩니다.
            """;

    private final AnthropicClient anthropicClient;
    private final ObjectMapper objectMapper;

    public AnthropicOrganizeClient(AnthropicClient anthropicClient, ObjectMapper objectMapper) {
        this.anthropicClient = anthropicClient;
        this.objectMapper = objectMapper;
    }

    @Override
    public OrganizeProposal proposeOrganization(List<FolderTreeNode> currentTree, List<OrganizeFileInput> files) {
        String prompt = buildUserPrompt(currentTree, files);

        MessageCreateParams params = MessageCreateParams.builder()
                .model(Model.CLAUDE_SONNET_5)
                .maxTokens(MAX_TOKENS)
                .system(SYSTEM_PROMPT)
                .addUserMessage(prompt)
                .build();

        Message message = anthropicClient.messages().create(params);
        String responseText = extractText(message);

        try {
            return objectMapper.readValue(stripCodeFence(responseText), OrganizeProposal.class);
        } catch (JsonProcessingException e) {
            throw new AiResponseParseException("AI 응답을 OrganizeProposal JSON으로 파싱하는 데 실패했습니다.", e);
        }
    }

    private String buildUserPrompt(List<FolderTreeNode> currentTree, List<OrganizeFileInput> files) {
        try {
            return "현재 폴더 트리:\n" + objectMapper.writeValueAsString(currentTree)
                    + "\n\n파일 목록:\n" + objectMapper.writeValueAsString(files);
        } catch (JsonProcessingException e) {
            throw new AiResponseParseException("프롬프트 입력을 JSON으로 직렬화하는 데 실패했습니다.", e);
        }
    }

    private String extractText(Message message) {
        return message.content().stream()
                .filter(ContentBlock::isText)
                .map(ContentBlock::asText)
                .map(TextBlock::text)
                .collect(Collectors.joining());
    }

    /** Claude가 지시를 무시하고 ```json ... ``` 코드블록으로 감싸 응답하는 경우를 방어한다. */
    private String stripCodeFence(String text) {
        String trimmed = text.trim();
        if (trimmed.startsWith("```")) {
            trimmed = trimmed.replaceFirst("^```(?:json)?\\s*", "");
            trimmed = trimmed.replaceFirst("```\\s*$", "");
        }
        return trimmed.trim();
    }
}
