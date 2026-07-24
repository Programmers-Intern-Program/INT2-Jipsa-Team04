package com.jipsa.auth;

import org.springframework.stereotype.Component;

import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;

/**
 * userId → 현재 role 인메모리 캐시.
 *
 * <p>JWT의 role claim은 발급 시점 값으로 고정돼 있어 권한 변경이 즉시 반영되지 않는다
 * ({@link JwtAuthenticationFilter} 참고). 매 요청마다 DB를 조회해 최신 role을 확인하면
 * 요청량만큼 DB 부하가 늘어나므로, 이 캐시로 "요청마다 최신 role 조회"를 메모리 조회로 대체한다.
 *
 * <p>쓰기({@link #put})는 role이 바뀌는 유일한 경로인 {@code AdminService.updateRole}에서만
 * 일어난다 — role 변경 시점에 정확히 캐시를 갱신하므로 별도의 만료(TTL) 정책은 두지 않는다.
 * 캐시 미스(서버 재시작 직후 등)는 호출자가 DB에서 한 번 채워 넣는다.
 *
 * <p><b>단일 인스턴스 배포를 전제로 한다.</b> 인스턴스가 여러 대로 늘어나면 인스턴스마다
 * 캐시가 따로 놀아 한 인스턴스에서 바뀐 role이 다른 인스턴스에 반영되지 않는 문제가 생긴다 —
 * 그때는 공유 캐시(Redis 등) 도입을 재검토해야 한다.
 */
@Component
public class UserRoleCache {

    private final Map<Long, String> roleByUserId = new ConcurrentHashMap<>();

    public Optional<String> get(Long userId) {
        return Optional.ofNullable(roleByUserId.get(userId));
    }

    public void put(Long userId, String role) {
        roleByUserId.put(userId, role);
    }
}
