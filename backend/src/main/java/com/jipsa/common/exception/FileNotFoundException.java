package com.jipsa.common.exception;

import org.springframework.http.HttpStatus;

public class FileNotFoundException extends ApiException {

    public FileNotFoundException(String message) {
        super(HttpStatus.NOT_FOUND, "FILE_NOT_FOUND", message);
    }
}