/**
 * bytes(sizeBytes)를 "2.4 MB" 같은 사람이 읽기 쉬운 문자열로 변환.
 * API 응답(File.sizeBytes)은 byte 단위 숫자이므로 화면 표시 시 이 함수를 거친다.
 */
export function formatBytes(bytes: number): string {
  if (!bytes || bytes <= 0) return "0 KB";

  const units = ["B", "KB", "MB", "GB", "TB"];
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / Math.pow(1024, exponent);
  const decimals = exponent === 0 ? 0 : 1;

  return `${value.toFixed(decimals)} ${units[exponent]}`;
}
