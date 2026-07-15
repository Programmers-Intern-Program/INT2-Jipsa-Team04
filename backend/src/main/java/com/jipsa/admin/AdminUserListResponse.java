package com.jipsa.admin;

import java.util.List;

public record AdminUserListResponse(List<AdminUserListItem> items, long total) {
}
