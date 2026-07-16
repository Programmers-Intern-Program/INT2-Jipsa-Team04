package com.jipsa.common;

public record SuccessResponse(boolean success) {

    public static SuccessResponse ok() {
        return new SuccessResponse(true);
    }
}