package com.jipsa.auth.google;

import com.google.api.client.googleapis.auth.oauth2.GoogleIdToken;
import com.google.api.client.googleapis.auth.oauth2.GoogleIdTokenVerifier;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.security.GeneralSecurityException;

/**
 * 구글 id_token 검증만 담당한다 — 그 외 책임은 없다.
 *
 * <p>유일한 역할: 원문 id_token 문자열을 신뢰할 수 있는 {@link GoogleUserInfo}로 바꾼다.
 * 무거운 검증(구글 JWKS에 대한 서명 검증, {@code iss}, {@code aud}, {@code exp})은
 * 구글 공식 {@link GoogleIdTokenVerifier}가 수행하고, 그 위에 이 클래스가 애플리케이션
 * 규칙 두 가지를 추가로 강제한다: {@code email_verified}가 true여야 하고 {@code sub}가
 * 있어야 한다. 토큰을 단순 Base64 디코딩으로 신뢰하는 일은 절대 없다.
 */
@Component
public class GoogleIdTokenValidator {

    private final GoogleIdTokenVerifier verifier;

    public GoogleIdTokenValidator(GoogleIdTokenVerifier verifier) {
        this.verifier = verifier;
    }

    /**
     * 구글 id_token을 검증해 신뢰할 수 있는 {@link GoogleUserInfo}로 변환한다.
     *
     * @param idTokenString 구글이 발급한 원문 id_token(JWT) 문자열
     * @return 검증을 통과한 구글 사용자 정보
     * @throws GoogleAuthException 토큰이 잘못되었거나, 서명/iss/aud/exp가 유효하지 않거나,
     *         이메일이 미검증이거나, sub가 없을 때
     */
    public GoogleUserInfo validate(String idTokenString) {
        GoogleIdToken idToken;
        try {
            // 서명 / iss / aud / exp 검증에 실패하면 verify()는 null을 반환한다.
            idToken = verifier.verify(idTokenString);
        } catch (GeneralSecurityException | IOException e) {
            throw new GoogleAuthException("Google id_token 검증 중 오류가 발생했습니다.");
        }
        if (idToken == null) {
            throw new GoogleAuthException("유효하지 않은 Google id_token입니다.");
        }

        GoogleIdToken.Payload payload = idToken.getPayload();

        Boolean emailVerified = payload.getEmailVerified();
        if (emailVerified == null || !emailVerified) {
            throw new GoogleAuthException("이메일이 검증되지 않은 Google 계정입니다.");
        }

        String sub = payload.getSubject();
        if (sub == null || sub.isBlank()) {
            throw new GoogleAuthException("Google id_token에 sub가 없습니다.");
        }

        String email = payload.getEmail();
        String name = (String) payload.get("name");
        String picture = (String) payload.get("picture");

        return new GoogleUserInfo(sub, email, true, name, picture);
    }
}
