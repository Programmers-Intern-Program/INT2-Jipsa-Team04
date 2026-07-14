package com.jipsa.common.exception;

import org.springframework.http.HttpStatus;

public class UploadLimitExceededException extends ApiException {

    public UploadLimitExceededException(String message) {
        super(HttpStatus.BAD_REQUEST, "UPLOAD_LIMIT_EXCEEDED", message);
    }
}