package com.jipsa.organize;

import java.util.List;

public record ProposeForUploadRequest(List<Long> fileIds) {
}
