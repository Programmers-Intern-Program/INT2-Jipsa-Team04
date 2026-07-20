package com.jipsa.common.crypto;

import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.util.Base64;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * AesGcmTextEncryptor 순수 단위 테스트 — Spring 컨텍스트 없이 생성자에 Base64 키를 직접 주입한다.
 */
class AesGcmTextEncryptorTest {

    // 32바이트(256-bit) 테스트 키. 실제 키가 아님.
    private static final String KEY_32 =
            Base64.getEncoder().encodeToString("0123456789abcdef0123456789abcdef".getBytes(StandardCharsets.UTF_8));

    @Test
    void encrypt_decrypt_왕복하면_원문이_복원된다() {
        AesGcmTextEncryptor enc = new AesGcmTextEncryptor(KEY_32);

        String cipher = enc.encrypt("홍길동");

        assertThat(cipher).startsWith("v1:");
        assertThat(cipher).doesNotContain("홍길동");
        assertThat(enc.decrypt(cipher)).isEqualTo("홍길동");
    }

    @Test
    void encrypt_같은_평문도_매번_다른_암호문이_된다() {
        AesGcmTextEncryptor enc = new AesGcmTextEncryptor(KEY_32);

        String a = enc.encrypt("same");
        String b = enc.encrypt("same");

        assertThat(a).isNotEqualTo(b);                 // 매번 새 IV(nonce)
        assertThat(enc.decrypt(a)).isEqualTo("same");
        assertThat(enc.decrypt(b)).isEqualTo("same");
    }

    @Test
    void decrypt_변조된_암호문은_실패한다() {
        AesGcmTextEncryptor enc = new AesGcmTextEncryptor(KEY_32);
        String cipher = enc.encrypt("secret");
        String tampered = cipher.substring(0, cipher.length() - 2)
                + (cipher.endsWith("A") ? "B" : "A") + "=";

        assertThatThrownBy(() -> enc.decrypt(tampered))
                .isInstanceOf(IllegalStateException.class);
    }

    @Test
    void 생성자_키가_없으면_기동_실패() {
        assertThatThrownBy(() -> new AesGcmTextEncryptor("  "))
                .isInstanceOf(IllegalStateException.class);
    }

    @Test
    void 생성자_키_길이가_32바이트가_아니면_실패() {
        String shortKey = Base64.getEncoder().encodeToString("too-short-key".getBytes(StandardCharsets.UTF_8));

        assertThatThrownBy(() -> new AesGcmTextEncryptor(shortKey))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("32바이트");
    }

    @Test
    void 생성자_Base64가_아니면_실패() {
        assertThatThrownBy(() -> new AesGcmTextEncryptor("!!! not base64 !!!"))
                .isInstanceOf(IllegalStateException.class);
    }
}
