package com.jipsa.user;

import com.jipsa.common.exception.ApiException;
import org.springframework.http.HttpStatus;

/**
 * 로그인 자체는 성공(구글 인증 통과)했으나 계정 상태 때문에 로그인이 허용되지 않는 경우.
 * 예: LOCKED/SUSPENDED/WITHDRAWN 상태, Users.del=true, 또는 탈퇴(OAuth del=true) 이력이
 * 있는 계정의 재로그인(자동 재가입/재활성화하지 않음).
 *
 * <p>{@link ApiException}을 상속해 GlobalExceptionHandler가 표준 오류 응답으로 403을 낸다.
 */
public class AccountLoginBlockedException extends ApiException {

    public AccountLoginBlockedException(String message) {
        super(HttpStatus.FORBIDDEN, "ACCOUNT_LOGIN_BLOCKED", message);
    }
}
