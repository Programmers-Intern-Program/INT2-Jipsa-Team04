package com.jipsa.common;

/** 요청 자체는 형식이 맞지만 도메인 규칙(순환 참조 등)을 위반한 경우 공통으로 던지는 예외. 400으로 매핑된다. */
public class BadRequestException extends RuntimeException {
    public BadRequestException(String message) {
        super(message);
    }
}
