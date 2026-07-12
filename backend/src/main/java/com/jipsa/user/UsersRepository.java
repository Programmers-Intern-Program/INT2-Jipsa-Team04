package com.jipsa.user;

import org.springframework.data.jpa.repository.JpaRepository;

public interface UsersRepository extends JpaRepository<Users, Long> {
    // Spring Data generates all the basic CRUD (save, findById, ...) for you.
    // We'll add finders here as features need them.
}
