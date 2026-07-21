package com.jipsa.organize;

import java.util.List;

/** 현재 폴더 트리(또는 제안 폴더 트리)를 프론트 미리보기에 내려줄 때 쓰는 중첩 트리 노드. */
public record FolderTreeNode(Long folderId, String name, List<FolderTreeNode> children) {
}
