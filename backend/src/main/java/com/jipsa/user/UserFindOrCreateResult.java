package com.jipsa.user;

/**
 * {@link UserService#findOrCreate(com.jipsa.auth.google.GoogleUserInfo)}의 결과.
 * 내부 {@link Users}와 신규 가입 여부({@code isNewUser})를 함께 전달한다 —
 * isNewUser 판정이 서비스 밖으로 명시적으로 나가도록 하는 결과 타입이다.
 */
public record UserFindOrCreateResult(Users user, boolean isNewUser) {
}
