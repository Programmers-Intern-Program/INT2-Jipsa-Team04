/**
 * 실패하면 지수 백오프로 재시도한다. documents/folders/settings 조회는 이미 로그인이 확정된
 * 뒤에만 실행되므로(App.tsx의 !user 가드), 실패는 "비로그인이라 401" 같은 정상 상황이 아니라
 * 네트워크 순단이나 백엔드 일시 장애일 가능성이 크다 — 그런 실패를 mock 데이터로 조용히
 * 가리는 대신, 자동으로 몇 번 더 시도해서 복구를 노려본다. 재시도를 다 써도 실패하면
 * 마지막 에러를 그대로 던지고, 호출부가 그 상태(빈 목록 유지 등)를 판단한다.
 */
export async function fetchWithRetry<T>(
  fn: () => Promise<T>,
  { maxAttempts = 3, baseDelayMs = 1000 }: { maxAttempts?: number; baseDelayMs?: number } = {}
): Promise<T> {
  let lastError: unknown;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      return await fn();
    } catch (err) {
      lastError = err;
      if (attempt === maxAttempts) break;
      const delayMs = baseDelayMs * 2 ** (attempt - 1);
      await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
  }
  throw lastError;
}
