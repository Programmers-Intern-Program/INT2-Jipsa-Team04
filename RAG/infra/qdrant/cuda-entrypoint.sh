#!/usr/bin/env bash

set -Eeuo pipefail

# ============================================================
# Jipsa TEI CUDA Entrypoint
# ============================================================
#
# NVIDIA Driver 6xx 계열에서는 nvidia-smi가 CUDA 지원 버전을
# 다음 형식으로 출력할 수 있습니다.
#
#   CUDA UMD Version: 13.3
#
# 일부 TEI 1.9 이미지의 기본 Entrypoint는 기존 형식인
# "CUDA Version"만 인식하여 Driver 버전을 잘못 판단할 수 있습니다.
#
# 이 스크립트는 다음 형식을 모두 인식합니다.
#
#   CUDA Version: 12.9
#   CUDA UMD Version: 13.3
#
# Host Driver가 CUDA 12.9.1 미만을 지원하는 경우에만
# cuda-compat-12-9 라이브러리를 활성화합니다.
# ============================================================


# ============================================================
# NVIDIA 실행 파일 경로 등록
# ============================================================
#
# 일부 Container Runtime 환경은 NVIDIA 실행 파일을
# /usr/local/nvidia/bin에 마운트하지만 PATH에는 추가하지 않습니다.
if [[ -d "/usr/local/nvidia/bin" ]]; then
    export PATH="${PATH}:/usr/local/nvidia/bin"
fi


# ============================================================
# NVIDIA Host Driver Library 등록
# ============================================================
#
# Host Driver Library가 /usr/local/nvidia/lib64에 마운트된 경우
# Linux 동적 링커가 해당 디렉터리를 인식하도록 등록합니다.
if [[ -d "/usr/local/nvidia/lib64" ]]; then
    echo "/usr/local/nvidia/lib64" \
        > "/etc/ld.so.conf.d/nvidia-host.conf"

    ldconfig
fi


# ============================================================
# NVIDIA GPU Runtime 확인
# ============================================================
#
# nvidia-smi를 실행할 수 없다면 GPU가 컨테이너에 정상적으로
# 전달되지 않은 상태이므로 CPU 폴백 없이 즉시 실패 처리합니다.
if ! command -v nvidia-smi >/dev/null 2>&1; then
    echo "Error: 'nvidia-smi' command not found." >&2
    echo "NVIDIA GPU Runtime이 TEI 컨테이너에 전달되지 않았습니다." >&2
    exit 1
fi


# ============================================================
# CUDA Compatibility Library 적용 여부 결정
# ============================================================
if [[ -d "/usr/local/cuda/compat" ]]; then
    # nvidia-smi 출력에서 다음 두 형식의 버전 숫자를 모두 추출합니다.
    #
    #   CUDA Version: 12.9
    #   CUDA UMD Version: 13.3
    DRIVER_CUDA="$(
        nvidia-smi 2>/dev/null \
            | grep -oE \
                'CUDA[[:space:]]+([A-Za-z]+[[:space:]])?Version:[[:space:]]*[0-9]+(\.[0-9]+)+' \
            | grep -oE \
                '[0-9]+(\.[0-9]+)+' \
            | head -n 1 \
            || true
    )"

    # 버전 감지에 실패한 상태로 compatibility library를 적용하면
    # 잘못된 CUDA Library가 우선 로드될 수 있으므로 중단합니다.
    if [[ -z "${DRIVER_CUDA}" ]]; then
        echo "Error: NVIDIA Driver CUDA 버전을 확인할 수 없습니다." >&2
        echo "nvidia-smi 출력:" >&2

        nvidia-smi >&2 || true

        exit 1
    fi

    # 감지한 CUDA 버전을 Major, Minor, Patch로 분리합니다.
    #
    # 예:
    #
    #   13.3   -> MAJ=13, MIN=3, PATCH=""
    #   12.9.1 -> MAJ=12, MIN=9, PATCH=1
    IFS='.' read -r MAJ MIN PATCH <<EOF
${DRIVER_CUDA}
EOF

    # nvidia-smi가 생략한 Minor 또는 Patch 값을 0으로 보정합니다.
    : "${MIN:=0}"
    : "${PATCH:=0}"

    # 산술식에 예상하지 못한 문자열이 들어가는 것을 방지합니다.
    if [[ ! "${MAJ}" =~ ^[0-9]+$ ]]; then
        echo "Error: CUDA Major 버전이 올바르지 않습니다: ${MAJ}" >&2
        exit 1
    fi

    if [[ ! "${MIN}" =~ ^[0-9]+$ ]]; then
        echo "Error: CUDA Minor 버전이 올바르지 않습니다: ${MIN}" >&2
        exit 1
    fi

    if [[ ! "${PATCH}" =~ ^[0-9]+$ ]]; then
        echo "Error: CUDA Patch 버전이 올바르지 않습니다: ${PATCH}" >&2
        exit 1
    fi

    # 버전을 비교 가능한 정수로 변환합니다.
    #
    # 반드시 Bash 산술 확장인 $(( ... )) 문법을 사용합니다.
    #
    # 10# 접두사는 08 또는 09 같은 값을 8진수로 오해하지 않고
    # 명시적으로 10진수로 처리합니다.
    #
    # 변환 예:
    #
    #   12.9.1 -> 120901
    #   13.3.0 -> 130300
    DRIVER_INT=$((10#${MAJ} * 10000 + 10#${MIN} * 100 + 10#${PATCH}))

    # TEI CUDA 이미지가 사용하는 CUDA Runtime 기준입니다.
    #
    # CUDA 12.9.1 -> 120901
    TARGET_INT=$((12 * 10000 + 9 * 100 + 1))

    echo "Detected NVIDIA Driver CUDA version: ${DRIVER_CUDA}"
    echo "Detected Driver version integer: ${DRIVER_INT}"
    echo "Required CUDA Runtime version integer: ${TARGET_INT}"

    if (( DRIVER_INT < TARGET_INT )); then
        # Host Driver의 CUDA 지원 버전이 12.9.1 미만일 때만
        # cuda-compat-12-9를 우선 검색 경로에 추가합니다.
        export LD_LIBRARY_PATH="/usr/local/cuda/compat:${LD_LIBRARY_PATH:-}"

        echo "Enabled CUDA compatibility library: /usr/local/cuda/compat"
    else
        # 현재 CUDA 13.3 환경에서는 compatibility library를
        # 사용하지 않고 Host Driver Library를 사용해야 합니다.
        echo "CUDA compatibility library is not required."
    fi
fi


# ============================================================
# TEI Router 실행
# ============================================================
#
# exec를 사용해 TEI Router가 컨테이너의 PID 1이 되도록 합니다.
# Docker 종료 신호가 Router 프로세스에 직접 전달됩니다.
exec text-embeddings-router "$@"