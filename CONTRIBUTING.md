# 개발 방법

이 저장소는 개인 홈페이지와 맥의 기록 편집기 소스를 비공개로 관리한다. 개발 기록은 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)에 남긴다.

## 실행과 검사

```bash
python3 scripts/setup_local.py
python3 scripts/build.py
python3 scripts/nginx_config.py
python3 -I scripts/check.py
```

편집기는 `./start-editor.command`로 실행한다. 사진 처리 의존성이 준비되면 다음을 실행한다.

```bash
.local/editor-venv/bin/python -m unittest discover -s tests -p 'test_*.py'
node --check assets/site.js
node --check editor/editor.js
python3 scripts/package.py
```

실제 nginx 실행 검증은 별도의 Linux 환경에서 수행한다. 단위 검증과 패키지 검사만으로 운영 배포 완료라고 기록하지 않는다.

## 커밋 전

변경한 소스 파일을 선별해 스테이징하고, Git 인덱스에 들어간 실제 내용을 확인한다.

```bash
git diff
git add <변경한-소스-파일>
python3 scripts/repo_check.py
gitleaks git --staged --no-banner --redact
git diff --cached --stat
git diff --cached
git commit
```

초안·사진·로그·검수 이미지·로컬 운영 문서를 강제로 추가하지 않는다. 기존 커밋 보안 훅을 유지하고 검사 실패 원인을 수정한다. GitHub Actions는 추적된 소스와 예제 데이터로 같은 검증을 수행한다.

커밋은 하나의 변경 이유를 설명할 수 있는 단위로 남긴다. 예를 들어 버그 수정과 새 기능을 독립적으로 검증할 수 있다면 나눈다. 진행 중인 작업이나 확인이 필요한 문제는 Issues에 기록한다.

## 작업 기준

- 사용자가 고민이나 의견을 말하면 먼저 논의하고, 수정에 동의하거나 작업을 요청했을 때 구현한다.
- 확정한 소개 문구와 현재 디자인을 유지한다. 이 프로젝트에는 Impeccable 지침을 적용하지 않는다.
- 공개 내용과 개인 데이터의 경계를 유지한다. 실제 글을 테스트용 공개 데이터로 사용하지 않는다.
- 공개본 준비와 인터넷 서버 반영은 별도 작업으로 다룬다.
- 큰 변경은 원인·결과·검증 방법을 개발 문서나 관련 Issue에 남긴다.

## 저장소 공개 전

현재 저장소는 비공개다. 공개 전환은 별도로 결정한다. 전체 커밋 이력·프로젝트 소개·운영 설정·검사 결과·Actions 로그와 작성자 이메일을 다시 확인한다. 예전에 지웠다는 이유로 개인정보가 이력에서도 사라졌다고 가정하지 않는다.
