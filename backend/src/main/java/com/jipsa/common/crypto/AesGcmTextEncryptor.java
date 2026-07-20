package com.jipsa.common.crypto;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import javax.crypto.Cipher;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.SecureRandom;
import java.util.Base64;

/**
 * 개인정보(예: UsersInformation.Name_Enc) 필드를 AES-GCM으로 암호화한다.
 *
 * <p>키는 코드/Git에 넣지 않고 환경변수 {@code JIPSA_FIELD_ENC_KEY}(Spring 프로퍼티
 * {@code jipsa.field-enc-key}로 relaxed-binding)에서 Base64로 읽는다. 부팅 시 키가
 * 없거나 256비트(32바이트)가 아니면 애플리케이션 기동을 실패시켜(fail-fast) 평문이
 * 저장되는 사고를 원천 차단한다.
 *
 * <p>저장 형식: {@code "v1:" + Base64( IV(12B) ‖ ciphertext ‖ GCM tag(16B) )}.
 * IV(nonce)는 매 암호화마다 SecureRandom으로 새로 생성한다(재사용 금지).
 * {@code v1:} 프리픽스는 향후 키 로테이션/포맷 변경을 위한 버전 표식이다.
 */
@Component
public class AesGcmTextEncryptor {

    private static final String TRANSFORMATION = "AES/GCM/NoPadding";
    private static final int KEY_BYTES = 32;        // 256-bit
    private static final int GCM_IV_BYTES = 12;     // 96-bit nonce (GCM 권장)
    private static final int GCM_TAG_BITS = 128;    // 인증 태그 128-bit
    private static final String VERSION_PREFIX = "v1:";

    private final SecretKey key;
    private final SecureRandom secureRandom = new SecureRandom();

    public AesGcmTextEncryptor(@Value("${jipsa.field-enc-key:}") String base64Key) {
        if (base64Key == null || base64Key.isBlank()) {
            throw new IllegalStateException(
                    "필드 암호화 키가 설정되지 않았습니다. 환경변수 JIPSA_FIELD_ENC_KEY(Base64 32바이트)를 설정하세요.");
        }
        byte[] keyBytes;
        try {
            keyBytes = Base64.getDecoder().decode(base64Key.trim());
        } catch (IllegalArgumentException e) {
            throw new IllegalStateException("JIPSA_FIELD_ENC_KEY 값이 올바른 Base64가 아닙니다.", e);
        }
        if (keyBytes.length != KEY_BYTES) {
            throw new IllegalStateException(
                    "JIPSA_FIELD_ENC_KEY는 256비트(32바이트)여야 합니다. 현재 길이: " + keyBytes.length + "바이트");
        }
        this.key = new SecretKeySpec(keyBytes, "AES");
    }

    /** 평문을 {@code v1:Base64(IV‖ct‖tag)} 형식 문자열로 암호화한다. */
    public String encrypt(String plaintext) {
        if (plaintext == null) {
            throw new IllegalArgumentException("암호화 대상 문자열이 null입니다.");
        }
        try {
            byte[] iv = new byte[GCM_IV_BYTES];
            secureRandom.nextBytes(iv);

            Cipher cipher = Cipher.getInstance(TRANSFORMATION);
            cipher.init(Cipher.ENCRYPT_MODE, key, new GCMParameterSpec(GCM_TAG_BITS, iv));
            byte[] ciphertext = cipher.doFinal(plaintext.getBytes(StandardCharsets.UTF_8));

            byte[] combined = ByteBuffer.allocate(iv.length + ciphertext.length)
                    .put(iv)
                    .put(ciphertext)
                    .array();
            return VERSION_PREFIX + Base64.getEncoder().encodeToString(combined);
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("필드 암호화에 실패했습니다.", e);
        }
    }

    /**
     * {@link #encrypt(String)}로 만든 문자열을 복호화한다. 3단계에서는 저장(암호화)만
     * 사용하지만, 이후 단계(/users/me 등)에서 재사용할 수 있도록 함께 제공한다.
     */
    public String decrypt(String stored) {
        if (stored == null || !stored.startsWith(VERSION_PREFIX)) {
            throw new IllegalArgumentException("지원하지 않는 암호문 형식입니다.");
        }
        try {
            byte[] combined = Base64.getDecoder().decode(stored.substring(VERSION_PREFIX.length()));
            ByteBuffer buffer = ByteBuffer.wrap(combined);

            byte[] iv = new byte[GCM_IV_BYTES];
            buffer.get(iv);
            byte[] ciphertext = new byte[buffer.remaining()];
            buffer.get(ciphertext);

            Cipher cipher = Cipher.getInstance(TRANSFORMATION);
            cipher.init(Cipher.DECRYPT_MODE, key, new GCMParameterSpec(GCM_TAG_BITS, iv));
            return new String(cipher.doFinal(ciphertext), StandardCharsets.UTF_8);
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("필드 복호화에 실패했습니다(키 불일치 또는 데이터 변조).", e);
        }
    }
}
