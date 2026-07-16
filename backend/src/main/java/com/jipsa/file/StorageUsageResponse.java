package com.jipsa.file;

public record StorageUsageResponse(
        long usedBytes,
        long quotaBytes
) {
}