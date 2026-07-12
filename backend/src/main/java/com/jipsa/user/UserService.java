package com.jipsa.user;

import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class UserService {

    private final UsersRepository usersRepository;
    private final OAuthConnectionsRepository oauthRepository;

    public UserService(UsersRepository usersRepository,
                       OAuthConnectionsRepository oauthRepository) {
        this.usersRepository = usersRepository;
        this.oauthRepository = oauthRepository;
    }

    /**
     * Look up the user behind a social login. If this (provider, providerUserId)
     * pair has logged in before, return the existing Users row; otherwise create
     * a new Users row and link it via a new OAuth_Connections row.
     *
     * @param provider        e.g. "GOOGLE"
     * @param providerUserId  the stable id the provider gives us for this account
     */
    @Transactional
    public Users findOrCreate(String provider, String providerUserId) {
        return oauthRepository
                .findByProviderAndProviderUserIdAndDelFalse(provider, providerUserId)
                .map(conn -> usersRepository.findById(conn.getUsersId())
                        .orElseThrow(() -> new IllegalStateException(
                                "OAuth connection points to a missing user: " + conn.getUsersId())))
                .orElseGet(() -> createUserWithConnection(provider, providerUserId));
    }

    private Users createUserWithConnection(String provider, String providerUserId) {
        Users user = new Users();          // role=USERS, status=ACTIVE, del=false by default
        usersRepository.save(user);        // IDENTITY id is assigned here

        OAuthConnection conn = new OAuthConnection();
        conn.setUsersId(user.getId());
        conn.setProvider(provider);
        conn.setProviderUserId(providerUserId);
        oauthRepository.save(conn);

        return user;
    }
}
