package com.jipsa.user;

import java.math.BigDecimal;

/** GET /api/v1/users/me/settings 응답 (API 문서.md 1장). */
public record UserSettingResponse(
        BigDecimal sensitivity,
        String voiceModel,
        String responseStyle,
        boolean instantSummary,
        boolean autoHighlight,
        boolean pushNotification
) {

    static UserSettingResponse from(UserSetting setting) {
        return new UserSettingResponse(
                setting.getSensitivity(),
                setting.getVoiceMode(),
                setting.getResponseStyle(),
                setting.isInstantSummary(),
                setting.isAutoHighlight(),
                setting.isPushNotification()
        );
    }
}
