# Issue #159 단계형 Stress 2차 수정 요약

## 오류 원인

`CommandNotFoundException`은 실행 코드 내부 오류가 아니라
`RAG-Performance/scripts/run-staged-stress-test.ps1`이 사용자 작업 경로에 존재하지 않아
발생했습니다. 이전 ZIP을 저장소 Root가 아닌 `RAG-Performance`에서 풀면서
`RAG-Performance/RAG-Performance/scripts`로 중첩됐거나, 신규 파일을 아직 덮어쓰지 않은
상태에서 나타날 수 있습니다.

이번 배포 ZIP은 **현재 `RAG-Performance` 폴더에 바로 압축 해제**하도록 archive root를
`scripts/`, `src/`, `configs/`, `tests/`, `README.md`, `README.html`로 구성합니다.

## 기능 수정

- `run-staged-stress-test.ps1` 전체 파일 포함
- Quick, Standard, Endurance, Destructive 모두 공통 1~5단계 보장
  1. Burst
  2. Interval
  3. Batch
  4. Ramp
  5. Chaos
- Profile별 Soak·인제스트 Ramp·Fault Suite 추가 단계 유지
- 내장 Profile의 첫 다섯 활성 Mode 순서를 Plan Parser가 검증
- 누락·순서 변경 회귀 테스트 추가

## README 자동 갱신

Stress Campaign 종료 시 기본적으로 다음 문서를 갱신합니다.

- `README.md`
- `README.html`

두 문서의 `STRESS-VERIFICATION` Marker 사이에 있는 `16. 마지막 검증 기록`만 교체합니다.

기록 내용:

- PASS·DEGRADED·FAIL
- Run ID, Profile, 완료 시각
- 품질 게이트 실행·생략 여부
- Stage·요청 통계
- 검색·인제스트 정상 최대와 최초 실패 동시성
- 인프라 복원과 Scope Guard
- 마지막 Markdown·HTML 보고서 링크

실패·Guard 중단도 마지막 상태로 기록합니다. README 변경을 남길 수 없는 읽기 전용 환경은
`-SkipReadmeUpdate`로 명시적으로 생략할 수 있습니다.

## 문서 개선

- `README.md`에 1~5단계와 네 Profile의 실제 수치 명시
- Script 미배치·중첩 압축 경로 문제 해결 절차 추가
- 자동 검증 기록의 동작과 Git 작업 트리 변경 이유 추가
- `README.html`에 동일 내용, Profile 비교 표, 그래프, 검색, Theme, 인쇄, Markdown 보기 유지
- `TEST_COMMANDS.md`에 적용 경로 확인부터 전체 실행·결과 확인까지 통합

## 제작 환경 검증

- Python 전체 문법 Compile: 통과
- 네 Stress Plan JSON Parse: 통과
- 네 Profile 공통 1~5단계 순서: 통과
- README HTML Strict Parse: 통과
- Pytest: 35 passed

Windows Python 3.12의 Ruff와 Mypy, CUDA 12.9·Docker·Local DB 실제 실행은 사용자 환경에서
최종 확인해야 합니다.
