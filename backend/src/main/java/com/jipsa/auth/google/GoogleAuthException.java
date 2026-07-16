package com.jipsa.auth.google;

import com.jipsa.common.exception.ApiException;
import org.springframework.http.HttpStatus;

/**
 * 구글 인증 실패 시 발생 — 토큰 엔드포인트 오류, id_token 누락, 또는 id_token 검증
 * 실패(서명/iss/aud/exp/sub/email_verified)인 경우.
 *
 * <p>{@link ApiException}을 상속해, 기존 GlobalExceptionHandler가 표준
 * {@code {success:false, error:{code,message}}} 형식으로 401 응답을 렌더링하게 한다.
 */
public class GoogleAuthException extends ApiException {

    public GoogleAuthException(String message) {
        super(HttpStatus.UNAUTHORIZED, "GOOGLE_AUTH_FAILED", message);
    }
}
