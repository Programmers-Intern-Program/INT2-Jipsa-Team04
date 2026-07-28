"""Windows spawn 자식 프로세스가 테스트 worker 모듈을 다시 import하게 한다.

Pytest의 ``--import-mode=importlib``는 테스트 디렉터리를 일반 Python package로
강제하지 않는다. 이 package marker와 ``tests/conftest.py``의 프로젝트 루트 경로
등록을 함께 사용하여 spawn worker가 테스트 모듈의 최상위 target 함수를 복원한다.
"""
