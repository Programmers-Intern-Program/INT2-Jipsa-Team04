package com.jipsa.auth.google;

import com.google.api.client.googleapis.auth.oauth2.GoogleIdToken;
import com.google.api.client.googleapis.auth.oauth2.GoogleIdTokenVerifier;
import com.google.api.client.json.webtoken.JsonWebSignature;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class GoogleIdTokenValidatorTest {

    @Mock
    private GoogleIdTokenVerifier verifier;

    private GoogleIdTokenValidator validator;

    @BeforeEach
    void setUp() {
        validator = new GoogleIdTokenValidator(verifier);
    }

    /** Builds a GoogleIdToken whose signature would already have been checked by verify(). */
    private GoogleIdToken tokenWith(String sub, String email, Boolean emailVerified) {
        GoogleIdToken.Payload payload = new GoogleIdToken.Payload();
        payload.setSubject(sub);
        payload.setEmail(email);
        payload.setEmailVerified(emailVerified);
        payload.set("name", "홍길동");
        payload.set("picture", "https://example.com/p.png");
        return new GoogleIdToken(new JsonWebSignature.Header(), payload, new byte[0], new byte[0]);
    }

    @Test
    void mapsVerifiedTokenToUserInfo() throws Exception {
        when(verifier.verify("valid"))
                .thenReturn(tokenWith("google-sub-123", "user@example.com", true));

        GoogleUserInfo info = validator.validate("valid");

        assertThat(info.sub()).isEqualTo("google-sub-123");
        assertThat(info.email()).isEqualTo("user@example.com");
        assertThat(info.emailVerified()).isTrue();
        assertThat(info.name()).isEqualTo("홍길동");
        assertThat(info.picture()).isEqualTo("https://example.com/p.png");
    }

    @Test
    void throwsWhenVerificationFails() throws Exception {
        // verify() returns null when signature / iss / aud / exp are invalid.
        when(verifier.verify("invalid")).thenReturn(null);

        assertThatThrownBy(() -> validator.validate("invalid"))
                .isInstanceOf(GoogleAuthException.class);
    }

    @Test
    void throwsWhenEmailNotVerified() throws Exception {
        when(verifier.verify("unverified"))
                .thenReturn(tokenWith("google-sub-123", "user@example.com", false));

        assertThatThrownBy(() -> validator.validate("unverified"))
                .isInstanceOf(GoogleAuthException.class);
    }

    @Test
    void throwsWhenSubMissing() throws Exception {
        when(verifier.verify("no-sub"))
                .thenReturn(tokenWith(null, "user@example.com", true));

        assertThatThrownBy(() -> validator.validate("no-sub"))
                .isInstanceOf(GoogleAuthException.class);
    }
}
