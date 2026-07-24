package com.jipsa.common.exception;

import org.springframework.http.HttpStatus;

public class RagUpstreamException extends ApiException {
    public RagUpstreamException(String message) {
        super(HttpStatus.BAD_GATEWAY, "RAG_BAD_RESPONSE", message);
    }
}