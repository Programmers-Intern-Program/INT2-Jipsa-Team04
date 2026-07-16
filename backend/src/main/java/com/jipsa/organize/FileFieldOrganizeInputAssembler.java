package com.jipsa.organize;

import com.jipsa.file.File;
import com.jipsa.file.FileRepository;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * v0 구현체 — File의 기존 필드(파일명, 확장자=fileType, 크기, 소속 폴더, 업로드일=createdAt)만 사용한다.
 * 요약/태그 등은 다루지 않는다(별도 파싱 파이프라인 완료 후 다른 구현체로 교체 예정).
 */
@Component
public class FileFieldOrganizeInputAssembler implements OrganizeInputAssembler {

    private final FileRepository fileRepository;

    public FileFieldOrganizeInputAssembler(FileRepository fileRepository) {
        this.fileRepository = fileRepository;
    }

    @Override
    public List<OrganizeFileInput> assemble(Long userId) {
        return fileRepository.findByUsersIdAndDeletedAtIsNull(userId).stream()
                .map(this::toInput)
                .toList();
    }

    private OrganizeFileInput toInput(File file) {
        return new OrganizeFileInput(
                file.getId(),
                file.getName(),
                file.getFileType(),
                file.getSizeBytes(),
                file.getFolderId(),
                file.getCreatedAt());
    }
}
