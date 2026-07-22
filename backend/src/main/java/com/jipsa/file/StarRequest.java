package com.jipsa.file;

import jakarta.validation.constraints.NotNull;

public record StarRequest(@NotNull Boolean star) {
}