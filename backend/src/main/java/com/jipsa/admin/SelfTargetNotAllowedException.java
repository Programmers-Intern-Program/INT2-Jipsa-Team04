package com.jipsa.admin;

import com.jipsa.common.exception.ApiException;
import org.springframework.http.HttpStatus;

/** 관리자가 자기 자신을 대상으로 정지/삭제/권한변경/해제를 시도한 경우. 마지막 관리자가 실수로
 *  스스로를 잠그는 상황을 막기 위한 가드. */
public class SelfTargetNotAllowedException extends ApiException {
    public SelfTargetNotAllowedException() {
        super(HttpStatus.BAD_REQUEST, "SELF_TARGET_NOT_ALLOWED", "자기 자신을 대상으로 할 수 없습니다.");
    }
}
