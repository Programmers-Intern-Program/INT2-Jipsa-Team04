package com.jipsa.admin;

public record SuccessResponse(boolean success) {

    static SuccessResponse ok() {
        return new SuccessResponse(true);
    }
}
