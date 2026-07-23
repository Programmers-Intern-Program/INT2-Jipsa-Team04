/**
 * ISO 날짜/시간 문자열("2026-07-22T15:56:41.926157")을 분 단위까지만
 * 잘라 "2026-07-22 15:56" 형태로 변환한다.
 * API 응답의 modifiedAt은 초 이하 자리수까지 포함돼 있으므로 화면 표시 시 이 함수를 거친다.
 */
export function formatDateTime(iso: string): string {
  if (!iso) return "-";
  return iso.slice(0, 16).replace("T", " ");
}
