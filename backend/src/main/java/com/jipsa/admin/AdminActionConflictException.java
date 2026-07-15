package com.jipsa.admin;

import com.jipsa.common.exception.ApiException;
import org.springframework.http.HttpStatus;

/** 대상 사용자가 이미 요청된 상태와 충돌하는 상태인 경우(이미 정지됨, 이미 삭제됨 등). */
public class AdminActionConflictException extends ApiException {
    public AdminActionConflictException(String message) {
        super(HttpStatus.CONFLICT, "ADMIN_ACTION_CONFLICT", message);
    }
}
