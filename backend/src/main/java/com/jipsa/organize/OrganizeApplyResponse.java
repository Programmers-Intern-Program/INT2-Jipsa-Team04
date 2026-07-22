package com.jipsa.organize;

import java.util.List;

/**
 * POST /api/v1/organize/apply 응답.
 * held: confidence가 사용자의 자동 분류 민감도(User_Setting.Auto_Classification_Sensitivity)보다
 * 낮아 자동 반영에서 제외되고 보류된 매핑 목록. 이 매핑에 해당하는 파일은 이동/이름변경되지 않고
 * 원래 위치에 그대로 남는다. 비어 있으면 모든 매핑이 그대로 반영됐다는 뜻이다.
 *
 * 같은 idempotencyKey로 재요청되어 반영이 조용히 스킵된 경우, held는 이번 호출 기준으로는
 * 항상 빈 목록이다(원래 반영 시점의 보류 목록은 다시 계산하지 않는다).
 */
public record OrganizeApplyResponse(boolean success, List<FileMapping> held) {

    public static OrganizeApplyResponse allApplied() {
        return new OrganizeApplyResponse(true, List.of());
    }
}
