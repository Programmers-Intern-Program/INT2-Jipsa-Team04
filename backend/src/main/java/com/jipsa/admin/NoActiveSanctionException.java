package com.jipsa.admin;

import com.jipsa.common.exception.ApiException;
import org.springframework.http.HttpStatus;

/** 해제(unsuspend) 대상인 ACTIVE 상태 제재 이력이 없는 경우. */
public class NoActiveSanctionException extends ApiException {
    public NoActiveSanctionException(Long userId) {
        super(HttpStatus.NOT_FOUND, "NO_ACTIVE_SANCTION", "해제할 활성 제재 이력이 없습니다: " + userId);
    }
}
