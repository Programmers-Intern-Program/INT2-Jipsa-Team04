package com.jipsa.user;

import jakarta.validation.constraints.DecimalMax;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.Size;

import java.math.BigDecimal;

/**
 * PATCH /api/v1/users/me/settings 요청. 6개 필드 모두 선택(부분 수정).
 * Folder의 PATCH(parentFolderId=null이 "루트로 이동"이라는 별도 의미를 가짐)와 달리,
 * User_Setting의 모든 컬럼은 DDL에서 NOT NULL이라 "필드를 null로 명시"가 애초에 유효한
 * 입력이 아니다. 그래서 "필드 미전송"과 "null 전송"을 구분하는 Map 방식이 필요 없고,
 * null = 변경 안 함으로 취급하는 단순 record면 충분하다.
 */
public record PatchUserSettingRequest(
        @DecimalMin("0.0") @DecimalMax("1.0") BigDecimal sensitivity,
        @Size(max = 20) String voiceModel,
        @Size(max = 20) String responseStyle,
        Boolean instantSummary,
        Boolean autoHighlight,
        Boolean pushNotification
) {
}
