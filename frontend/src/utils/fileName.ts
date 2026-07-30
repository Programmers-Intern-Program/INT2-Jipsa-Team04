export function normalizeExtension(fileType: string): string {
  return fileType.trim().replace(/^\./, "").toLowerCase();
}

export function getBaseName(fileName: string, fileType: string): string {
  const extension = normalizeExtension(fileType);
  const suffix = extension ? `.${extension}` : "";
  if (suffix && fileName.toLowerCase().endsWith(suffix)) {
    return fileName.slice(0, -suffix.length);
  }
  return fileName;
}

export function buildFileName(baseName: string, fileType: string): string {
  const extension = normalizeExtension(fileType);
  if (!extension) return baseName;
  const suffix = `.${extension}`;
  const normalizedBaseName = baseName.toLowerCase().endsWith(suffix)
    ? baseName.slice(0, -suffix.length)
    : baseName;
  return `${normalizedBaseName}${suffix}`;
}
