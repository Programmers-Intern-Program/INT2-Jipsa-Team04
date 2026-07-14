package com.jipsa.folder;

public record SuccessResponse(boolean success) {

    static SuccessResponse ok() {
        return new SuccessResponse(true);
    }
}
