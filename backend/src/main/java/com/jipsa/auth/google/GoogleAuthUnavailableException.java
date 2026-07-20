package com.jipsa.auth.google;

import com.jipsa.common.exception.ApiException;
import org.springframework.http.HttpStatus;

/**
 * 구글 인증 실패가 <b>클라이언트의 잘못된 인증 정보 때문이 아니라</b> 구글 측 장애나
 * 네트워크 장애 때문일 때 발생 — 사용자에게 "인증 정보가 틀렸다"(401)로 잘못 보고하지
 * 않도록 별도 예외로 분리한다.
 *
 * <ul>
 *   <li>구글 토큰 엔드포인트가 5xx를 주거나 응답 자체가 비정상이면 502 Bad Gateway.
 *   <li>timeout·DNS 실패·connection refused·I/O 오류로 구글에 닿지 못하면 503 Service Unavailable.
 * </ul>
 *
 * <p>{@link ApiException}을 상속해, 기존 GlobalExceptionHandler가 표준
 * {@code {success:false, error:{code,message}}} 형식으로 status에 맞춰 응답을 렌더링한다.
 */
public class GoogleAuthUnavailableException extends ApiException {

    private GoogleAuthUnavailableException(HttpStatus status, String code, String message) {
        super(status, code, message);
    }

    /** 구글이 5xx를 반환했거나 응답이 비정상이라 신뢰할 수 없을 때(upstream 오류). */
    public static GoogleAuthUnavailableException badGateway(String message) {
        return new GoogleAuthUnavailableException(
                HttpStatus.BAD_GATEWAY, "GOOGLE_AUTH_UPSTREAM_ERROR", message);
    }

    /** timeout·DNS·connection·I/O 오류로 구글에 닿지 못했을 때(연동 불가). */
    public static GoogleAuthUnavailableException serviceUnavailable(String message) {
        return new GoogleAuthUnavailableException(
                HttpStatus.SERVICE_UNAVAILABLE, "GOOGLE_AUTH_UNREACHABLE", message);
    }
}
