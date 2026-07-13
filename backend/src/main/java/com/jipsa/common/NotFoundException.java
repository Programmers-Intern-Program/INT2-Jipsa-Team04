package com.jipsa.common;

/** 요청한 리소스가 없거나(존재하지 않음) 요청자 소유가 아닌 경우 공통으로 던지는 예외. 404로 매핑된다. */
public class NotFoundException extends RuntimeException {
    public NotFoundException(String message) {
        super(message);
    }
}
