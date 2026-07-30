package com.jipsa.organize;

import java.util.List;

public record OrganizeApplyResponse(boolean success, List<FileMapping> held) {

    public static OrganizeApplyResponse allApplied() {
        return new OrganizeApplyResponse(true, List.of());
    }
}
