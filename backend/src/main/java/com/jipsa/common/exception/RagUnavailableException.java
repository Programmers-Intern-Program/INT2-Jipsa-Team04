package com.jipsa.common.exception;

import org.springframework.http.HttpStatus;

public class RagUnavailableException extends ApiException {
    public RagUnavailableException(String message) {
        super(HttpStatus.SERVICE_UNAVAILABLE, "RAG_UNAVAILABLE", message);
    }
}