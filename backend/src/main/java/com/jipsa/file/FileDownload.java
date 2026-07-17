package com.jipsa.file;

import org.springframework.core.io.Resource;

public record FileDownload(Resource resource, String filename, String contentType, long contentLength) {
}