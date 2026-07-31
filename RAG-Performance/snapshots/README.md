# Local Qdrant Snapshots

외부 RAG 성능 테스트의 데이터 자동 선정 Fallback에 사용할 Qdrant Collection Snapshot을
이 폴더에 둡니다.

```text
RAG-Performance/snapshots/*.snapshot
```

- `*.snapshot`은 Git에 커밋하지 않습니다.
- 운영 Qdrant Collection에 복원하지 않습니다.
- 실행기는 임시 Qdrant Container와 임시 Collection을 사용합니다.
- 실행 중인 `jipsa-qdrant` Container의 Docker Image를 우선 재사용합니다.
- 선정이 끝나면 임시 Container를 반드시 제거합니다.
