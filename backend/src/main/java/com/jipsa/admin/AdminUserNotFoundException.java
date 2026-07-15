package com.jipsa.admin;

import com.jipsa.common.exception.ApiException;
import org.springframework.http.HttpStatus;

public class AdminUserNotFoundException extends ApiException {
    public AdminUserNotFoundException(Long userId) {
        super(HttpStatus.NOT_FOUND, "ADMIN_USER_NOT_FOUND", "사용자를 찾을 수 없습니다: " + userId);
    }
}
