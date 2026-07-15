package com.jipsa.user;

public record SuccessResponse(boolean success) {

    static SuccessResponse ok() {
        return new SuccessResponse(true);
    }
}
