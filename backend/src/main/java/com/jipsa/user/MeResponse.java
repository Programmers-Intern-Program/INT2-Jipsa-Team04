package com.jipsa.user;

/**
 * GET /api/v1/users/me 응답. 로그인한 사용자의 기본 프로필.
 *
 * <p>API 명세의 {@code {name, profileImageUrl, role, status}}에 더해, 프론트가 JWT sub를 직접
 * 디코드해 우회하던 {@code userId}를 함께 내려준다(프론트 계약 편의). {@code role}/{@code status}는
 * DB의 실제 값({@code USERS}/{@code ADMIN}, {@code ACTIVE} 등)을 <b>그대로</b> 전달한다 —
 * 목업의 "Premium Plan" 같은 표시용 문자열을 만들지 않는다.
 *
 * <p>{@code name}은 {@link UsersInformation#getNameEnc()}(AES-GCM 암호문)를 복호화한 평문이다.
 */
public record MeResponse(
        Long userId,
        String name,
        String profileImageUrl,
        String role,
        String status
) {
}
