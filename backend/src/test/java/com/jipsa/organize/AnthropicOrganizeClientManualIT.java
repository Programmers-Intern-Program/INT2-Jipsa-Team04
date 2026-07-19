package com.jipsa.organize;

import com.anthropic.client.AnthropicClient;
import com.anthropic.client.okhttp.AnthropicOkHttpClient;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Disabled;
import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 실제 Anthropic API를 호출해서 AnthropicOrganizeClient가 진짜로 동작하는지 확인하는 수동 테스트.
 *
 * DB/인증 없이 AnthropicOrganizeClient만 직접 생성해서 호출한다 — 로그인/회원가입 API가
 * 아직 연결되어 있지 않아(OAuth2만 있고 미완성) HTTP 레벨로 /organize/propose를 직접 찌르는
 * 종단 테스트는 아직 불가능하다. 대신 이 클래스가 실제로 검증하고 싶은 부분(Claude 호출 +
 * 응답 JSON 파싱)만 골라서 확인한다.
 *
 * 기본적으로 비활성화되어 있다 — 실행하려면:
 *   1. 로컬 셸에 ANTHROPIC_API_KEY를 export
 *   2. 아래 @Disabled를 지우고 이 테스트 하나만 실행(IDE 우클릭 실행 등)
 *   3. 확인 후 다시 @Disabled를 붙여서 커밋
 * 일반 ./gradlew test 스위트에는 포함시키지 않는다 — 실행할 때마다 실제 API 비용이 들고,
 * 네트워크가 필요하며, 모델 응답이 결정적이지 않기 때문이다.
 */
@Disabled("ANTHROPIC_API_KEY가 있는 로컬 환경에서 수동으로만 실행")
class AnthropicOrganizeClientManualIT {

    @Test
    void 실제_Claude_호출이_유효한_OrganizeProposal_JSON으로_돌아온다() {
        AnthropicClient anthropicClient = AnthropicOkHttpClient.fromEnv();
        AnthropicOrganizeClient client = new AnthropicOrganizeClient(anthropicClient, new ObjectMapper());

        List<FolderTreeNode> currentTree = List.of(
                new FolderTreeNode(1L, "영수증", List.of()),
                new FolderTreeNode(2L, "계약서", List.of()));

        List<OrganizeFileInput> files = List.of(
                new OrganizeFileInput(10L, "2024년_1월_전기세.pdf", "pdf", 102_400L, null, LocalDateTime.now()),
                new OrganizeFileInput(11L, "회사_임대차계약서.pdf", "pdf", 204_800L, 2L, LocalDateTime.now()),
                new OrganizeFileInput(12L, "휴가사진.jpg", "jpg", 5_000_000L, null, LocalDateTime.now()));

        OrganizeProposal proposal = client.proposeOrganization(currentTree, files);

        System.out.println("newFolders = " + proposal.newFolders());
        System.out.println("mappings = " + proposal.mappings());

        assertThat(proposal).isNotNull();
        assertThat(proposal.newFolders()).isNotNull();
        assertThat(proposal.mappings()).isNotNull();
    }
}
