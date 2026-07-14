package com.jipsa.common.exception;

import org.springframework.http.HttpStatus;

public class UnsupportedFileTypeException extends ApiException {

    public UnsupportedFileTypeException(String message) {
        super(HttpStatus.BAD_REQUEST, "UNSUPPORTED_FILE_TYPE", message);
    }
}