package com.jipsa.organize;

/**
 * AI 응답 텍스트를 OrganizeProposal JSON으로 파싱하는 데 실패했을 때(또는 프롬프트 입력
 * 직렬화에 실패했을 때). 클라이언트 요청 자체는 문제가 없으므로 BadRequestException이 아니라
 * 별도 예외로 두고, GlobalExceptionHandler의 기본 처리(500)에 맡긴다.
 */
public class AiResponseParseException extends RuntimeException {
    public AiResponseParseException(String message, Throwable cause) {
        super(message, cause);
    }
}
