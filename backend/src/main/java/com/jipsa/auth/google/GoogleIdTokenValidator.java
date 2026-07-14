package com.jipsa.auth.google;

import com.google.api.client.googleapis.auth.oauth2.GoogleIdToken;
import com.google.api.client.googleapis.auth.oauth2.GoogleIdTokenVerifier;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.security.GeneralSecurityException;

/**
 * Verifies Google id_tokens — and nothing else.
 *
 * <p>Sole responsibility: turn a raw id_token string into a trusted {@link GoogleUserInfo}.
 * The heavy lifting (signature against Google's JWKS, {@code iss}, {@code aud}, {@code exp})
 * is done by Google's official {@link GoogleIdTokenVerifier}; on top of that this class
 * enforces our two application rules: {@code email_verified} must be true and {@code sub}
 * must be present. The token is never trusted by naive Base64 decoding.
 */
@Component
public class GoogleIdTokenValidator {

    private final GoogleIdTokenVerifier verifier;

    public GoogleIdTokenValidator(GoogleIdTokenVerifier verifier) {
        this.verifier = verifier;
    }

    /**
     * @throws GoogleAuthException if the token is malformed, its signature/iss/aud/exp
     *         are invalid, the email is unverified, or the sub is missing.
     */
    public GoogleUserInfo validate(String idTokenString) {
        GoogleIdToken idToken;
        try {
            // verify() returns null when signature / iss / aud / exp checks fail.
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
