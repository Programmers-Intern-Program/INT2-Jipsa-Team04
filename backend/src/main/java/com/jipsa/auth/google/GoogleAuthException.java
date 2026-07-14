package com.jipsa.auth.google;

import com.jipsa.common.exception.ApiException;
import org.springframework.http.HttpStatus;

/**
 * Raised when Google authentication fails — token-endpoint error, missing id_token,
 * or an id_token that fails verification (signature/iss/aud/exp/sub/email_verified).
 *
 * <p>Extends {@link ApiException} so the existing GlobalExceptionHandler renders it
 * as the standard {@code {success:false, error:{code,message}}} payload with 401.
 */
public class GoogleAuthException extends ApiException {

    public GoogleAuthException(String message) {
        super(HttpStatus.UNAUTHORIZED, "GOOGLE_AUTH_FAILED", message);
    }
}
