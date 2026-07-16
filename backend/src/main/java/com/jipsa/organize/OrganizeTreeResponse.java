package com.jipsa.organize;

import java.util.List;

/** GET /api/v1/organize/current-tree 응답: {folders:[...]}. */
public record OrganizeTreeResponse(List<FolderTreeNode> folders) {
}
