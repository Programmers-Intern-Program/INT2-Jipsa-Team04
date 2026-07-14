package com.jipsa.folder;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.hibernate.annotations.CreationTimestamp;
import org.hibernate.annotations.UpdateTimestamp;

import java.time.LocalDateTime;

@Entity
@Table(name = "Folder")          // exact table name from the DDL (case matters on Linux MySQL)
@Getter
@Setter
@NoArgsConstructor
public class Folder {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)   // AUTO_INCREMENT
    @Column(name = "Folder_IDX")
    private Long id;

    @Column(name = "Users_IDX", nullable = false)
    private Long usersId;               // FK to Users.Users_IDX (kept as a plain id for simplicity)

    @Column(name = "Parent_Folder_IDX")
    private Long parentFolderId;        // FK to Folder.Folder_IDX (self). NULL = root folder

    @Column(name = "Name", nullable = false, length = 255)
    private String name;

    @CreationTimestamp
    @Column(name = "Created_At", updatable = false)
    private LocalDateTime createdAt;

    @UpdateTimestamp
    @Column(name = "Updated_At")
    private LocalDateTime updatedAt;

    // NOTE: no `Del`/soft-delete column on this table's DDL — Folder deletion is a hard delete.
    public Folder(Long usersId, String name, Long parentFolderId) {
        this.usersId = usersId;
        this.name = name;
        this.parentFolderId = parentFolderId;
    }
}
