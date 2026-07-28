package com.jipsa.file;

import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.mockito.Mockito;
import org.springframework.mock.web.MockMultipartFile;
import software.amazon.awssdk.core.sync.RequestBody;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.PutObjectRequest;
import software.amazon.awssdk.services.s3.presigner.S3Presigner;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;

class S3ServiceTest {

    @Test
    void mapsPdfContentType() {
        assertContentType("a.pdf", null, "application/pdf");
    }

    @Test
    void mapsTxtContentType() {
        assertContentType("a.txt", null, "text/plain");
    }

    @Test
    void mapsDocxContentType() {
        assertContentType("a.docx", null,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document");
    }

    @Test
    void mapsPptxContentType() {
        assertContentType("a.pptx", null,
                "application/vnd.openxmlformats-officedocument.presentationml.presentation");
    }

    @Test
    void mapsXlsxContentType() {
        assertContentType("a.xlsx", null,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
    }

    @Test
    void ignoresExtensionCaseWhenMapping() {
        assertContentType("REPORT.PDF", null, "application/pdf");
    }

    @Test
    void fallsBackToProvidedContentTypeForUnknownExtension() {
        assertContentType("a.bin", "image/png", "image/png");
    }

    @Test
    void fallsBackToOctetStreamWhenNoExtensionOrType() {
        assertContentType("noext", null, "application/octet-stream");
    }

    private void assertContentType(String filename, String providedType, String expected) {
        S3Client s3Client = Mockito.mock(S3Client.class);
        S3Presigner s3Presigner = Mockito.mock(S3Presigner.class);
        S3Service s3Service = new S3Service(s3Client, s3Presigner);
        MockMultipartFile file = new MockMultipartFile("files", filename, providedType, new byte[]{1, 2, 3});

        s3Service.upload("bucket", "key", file);

        ArgumentCaptor<PutObjectRequest> captor = ArgumentCaptor.forClass(PutObjectRequest.class);
        verify(s3Client).putObject(captor.capture(), any(RequestBody.class));
        assertThat(captor.getValue().contentType()).isEqualTo(expected);
    }
}
