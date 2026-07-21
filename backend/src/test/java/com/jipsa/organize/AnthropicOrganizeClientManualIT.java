package com.jipsa.organize;

import com.jipsa.auth.JwtService;
import com.jipsa.file.File;
import com.jipsa.file.FileRepository;
import com.jipsa.folder.FolderService;
import org.junit.jupiter.api.Disabled;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.transaction.annotation.Transactional;

import java.util.UUID;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 실제 HTTP로 POST /api/v1/organize/propose를 태워서 인증 → 컨트롤러 → 서비스 검증 →
 * 실제 Anthropic 호출 → 응답 JSON 파싱까지 전체 배선이 실제로 동작하는지 확인하는 수동 테스트.
 *
 * 예전 버전은 AnthropicOrganizeClient만 직접 new해서 호출했다 — 그 방식은 Spring이 관리하는
 * 빈 배선(예: AnthropicConfig.objectMapper()의 JavaTimeModule 등록)을 전혀 거치지 않아서,
 * 실제로 있었던 버그(파일이 하나라도 있으면 propose가 500)를 이 테스트가 놓쳤었다.
 * 이번엔 MockMvc로 실제 컨트롤러·보안 필터 체인을 그대로 태워서 검증한다
 * (addFilters 기본값 true라 JwtAuthenticationFilter도 실제로 거친다).
 *
 * 기본적으로 비활성화되어 있다 — 실행하려면:
 *   1. 로컬 셸에 ANTHROPIC_API_KEY를 export
 *   2. 아래 @Disabled를 지우고 이 테스트 하나만 실행(IDE 우클릭 실행 등)
 *   3. 확인 후 다시 @Disabled를 붙여서 커밋
 * 일반 ./gradlew test 스위트에는 포함시키지 않는다 — 실행할 때마다 실제 API 비용이 들고,
 * 네트워크가 필요하며, 모델 응답이 결정적이지 않기 때문이다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Disabled("ANTHROPIC_API_KEY가 있는 로컬 환경에서 수동으로만 실행")
class AnthropicOrganizeClientManualIT {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private JwtService jwtService;

    @Autowired
    private FolderService folderService;

    @Autowired
    private FileRepository fileRepository;

    @Test
    @Transactional
    void 실제_HTTP로_propose를_태우면_유효한_OrganizeProposal_JSON이_돌아온다() throws Exception {
        // 이 테스트 전용 가상 사용자 id — File/Folder의 usersId는 Users 테이블에 대한 FK가
        // 아니라 단순 컬럼이라(OrganizeServiceIntegrationTest와 동일 전제) 실제 Users row 없이도 된다.
        Long userId = 9_999_001L;
        Long receiptFolder = folderService.create(userId, "영수증", null);
        saveFile(userId, "2024년_1월_전기세.pdf", null);
        saveFile(userId, "회사_임대차계약서.pdf", receiptFolder);

        String token = jwtService.generateToken(userId, "USERS");

        MvcResult result = mockMvc.perform(post("/api/v1/organize/propose")
                        .header("Authorization", "Bearer " + token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.newFolders").exists())
                .andExpect(jsonPath("$.mappings").exists())
                .andReturn();

        System.out.println("propose 응답 = " + result.getResponse().getContentAsString());
    }

    private void saveFile(Long userId, String name, Long folderId) {
        File file = new File();
        file.setUsersId(userId);
        file.setName(name);
        file.setS3Key("files/" + UUID.randomUUID());
        file.setFileType("pdf");
        file.setSizeBytes(100L);
        file.setFolderId(folderId);
        fileRepository.save(file);
    }
}
