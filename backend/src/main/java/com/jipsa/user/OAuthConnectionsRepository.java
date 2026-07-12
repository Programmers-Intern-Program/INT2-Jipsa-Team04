package com.jipsa.user;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface OAuthConnectionsRepository extends JpaRepository<OAuthConnection, Long> {

    // Used by the (Increment 2) find-or-create login logic:
    // "is there already an active connection for this Google user?"
    Optional<OAuthConnection> findByProviderAndProviderUserIdAndDelFalse(
            String provider, String providerUserId);
}
