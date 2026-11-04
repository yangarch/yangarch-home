# 비공개 방문 통계

공개 홈페이지의 nginx 접속 로그와 자체 호스팅 Umami를 함께 사용한다. 관리 화면과 데이터베이스를 인터넷에 공개하지 않는다. 맥 편집기의 **방문 통계**에서 접속 로그 집계를 읽고 **방문 분석 화면 열기**로 상세 화면에 연결한다. SSH 키 인증에 더해 Umami 관리자 로그인이 필요하다.

## 수집 범위

| 자료 | 항목 | 보관 |
| --- | --- | --- |
| 홈페이지 전용 접속 로그 | 시간, IP, 메서드, 경로, 응답 코드, 전송량, 응답 시간, 유입 사이트, User-Agent, 자동 요청 추정, 분석 제외 여부 | 일별 회전 14회, 오래된 회전 로그 최대 14일 |
| 방문 분석 | 페이지 조회, 서비스·프로젝트·출처·주요 메뉴 클릭, 분류 선택, 스크롤 25/50/75/100%, 활성 시간 15/30/60/120/300초, 화면 크기, 언어, 시간대, 유입 사이트와 제한된 캠페인 값 | 90일 |
| Umami 접속 환경 | User-Agent에서 추정한 브라우저·OS·기기, IP에서 추정한 국가·지역·도시 | 90일 |
| 일별 집계 | 요청·오류·자동 요청·일별 추정 방문자 수, 페이지별 조회 수, 이벤트 종류별 횟수 | 365일 |

원본 IP는 접속 로그에만 보관한다. Umami에는 원본 IP 대신 IP·User-Agent와 일별로 바뀌는 salt로 구분한 세션이 저장된다. 방문자·지역·시간은 추정치이며, 여러 날의 방문자를 합하면 같은 사람이 중복될 수 있다. 동일한 외부 IP와 브라우저를 사용하는 사람은 하나로 추정될 수 있다. 봇 판별 역시 User-Agent에 기반한 추정이다.

수집 요청과 접속 로그에서 URL query 및 referrer의 상세 경로를 제거한다. 분석에는 `utm_source`, `utm_medium`, `utm_campaign` 중 영문·숫자·밑줄·점·하이픈으로 된 최대 80자만 허용한다. 캠페인 이름에도 개인정보를 넣지 않는다. 자유 입력 이벤트, 광고 클릭 ID, persistent user ID, 위치 권한 요청, 화면 녹화, 키 입력, 기기 지문은 사용하지 않는다. HTTP 오류 진단은 nginx의 별도 운영 로그 정책을 따른다.

공개 [방문 정보 안내](https://yangarch.net/privacy/)에서 수집 항목과 보관 기간을 설명한다. Do Not Track, Global Privacy Control 및 브라우저별 분석 제외 선택을 존중한다. 편집기에서 **이 브라우저 방문 제외**를 한 번 열면 본인 브라우저를 분석에서 제외한다. 다른 브라우저에서는 다시 선택한다. 제외해도 서비스 보호용 접속 로그는 남는다. 제외 선택만 쿠키와 로컬 저장소에 1년 보관한다.

## 연결 구조

```text
방문자 → nginx /__analytics (POST만) → 127.0.0.1:4318 검증기
                                             ↓
                                 127.0.0.1:4319 내부 연결
                                             ↓
                                    Umami → PostgreSQL

맥 편집기 → SSH → 읽기 전용 집계 JSON
맥 브라우저 → 127.0.0.1:4390 → SSH → 127.0.0.1:4319 → Umami 로그인
```

Umami와 PostgreSQL에는 Docker `ports`를 설정하지 않는다. Umami만 호스트가 도달할 수 있는 전용 bridge에도 연결하고, DB는 별도의 internal network에 둔다. 호스트의 비권한 Python relay만 `127.0.0.1:4319`에서 앱 내부 주소로 연결한다. 이는 Docker 28 이전 버전의 localhost published-port 예외에 의존하지 않기 위한 구성이다. 다른 컨테이너에 네트워크를 공유하지 않는다.

외부로 프록시하는 것은 수집 전용 POST 하나다. 관리자·로그인·조회·replay API는 공개하지 않는다. nginx가 실제 접속 IP를 덮어쓰며 전달받은 IP·Cookie·Authorization 헤더를 신뢰하지 않는다. 검증기는 현재 공개된 HTML 경로, 고정 이벤트·값·크기를 확인하고 실제 HTML에서 제목을 읽는다. Umami 응답의 토큰과 세션 값은 브라우저에 반환하지 않는다. 방문 분석 실패는 홈페이지 탐색을 막지 않는다.

## 최초 준비와 적용

1. 서버 운영자 계정에서 검토한 `scripts/analytics_runtime.py`를 실행한다. 기존 Docker 권한만 사용하며, 고정 digest의 Umami 3.4.1 및 PostgreSQL 17 이미지를 준비한다. 전용 비밀값을 생성하고 기본 관리자 계정을 무작위 비밀번호로 교체한다. 웹사이트 공개 공유와 recorder는 켜지 않는다. 기존 설정이 있으면 덮어쓰지 않는다.
2. macOS에서 SSH 키 로그인을 확인한다. `.local/editor/publish.json`의 `ssh_host` 별칭은 배포와 통계 조회에 함께 사용한다.
3. `python3 scripts/package_analytics.py`를 실행하기 전에 현재 선택된 공개본을 build → nginx_config → check 순서로 검사한다. 생성한 `artifacts/analytics-setup.tar.gz`는 비공개 설치 자료다. 배포 준비 폴더에 검증된 정적 파일, manifest, 검토된 설치·분석·수신기 모듈을 복사한다. 초안·운영 비밀값은 포함하지 않는다.
4. 서버에서 **한 번만** `sudo bash deploy/setup-analytics.sh`를 실행한다. systemd 수집기·relay·집계 timer, 전용 로그와 회전, nginx apex 설정, 보안 헤더, 배포 수신기를 설치한다. 선택된 정적 파일과 안내 페이지도 함께 게시한다. 오류 시 이전 홈페이지·nginx·수신기 설정과 서비스 상태를 복원한다. 설치 백업은 root 전용이다.
5. 편집기를 재시작한 뒤 **방문 통계**를 연다. **방문 분석 화면 열기**로 연결하고 비공개 로그인 파일의 초기 정보를 사용한다. Umami 설정에서 비밀번호 변경과 2단계 인증을 설정할 수 있다. 초기 파일은 별도 보관 후 삭제해도 되며 편집기에는 비밀번호를 저장하지 않는다. 관리자 비밀번호를 바꾼 후에는 bootstrap을 무심코 다시 실행하지 않는다.

서버의 `~/.config/yangarch-home-analytics/`에는 mode 600 설정·compose·초기 로그인 파일을, `~/.local/share/yangarch-home-analytics/`에는 mode 600 집계를 둔다. 맥에 전달한 초기 로그인 파일은 `.local/editor/analytics-login.json`에 보관한다. 모두 Git과 웹 루트에서 제외한다. 실제 비밀번호를 명령 인자·출력·문서에 넣지 않는다.

## 운영

- `yangarch-home-analytics.service`: 비권한 수집 검증기, loopback 전용.
- `yangarch-home-analytics-proxy.service`: 비권한 내부 연결, loopback 전용.
- `yangarch-home-analytics-maintenance.timer`: 부팅 후 및 매시간 집계·보관 기간 정리. 편집기의 새로고침은 마지막 집계를 읽으며 즉시 전체 로그를 재처리하지 않는다. 최신 방문 흐름은 Umami 화면에서 확인한다.
- `/var/log/yangarch-home/access.log`: mode 640, `www-data:adm`. 다른 서비스의 공용 로그는 읽거나 합치지 않는다. loopback 검수 요청은 이 전용 로그에서 제외한다.
- 최근 100개 요청만 비공개 집계에 포함한다. 화면은 최대 50개를 표시하며 IP는 별도 보기 버튼으로 표시한다. 원본 로그 파싱은 64 MB 한도이며 초과 시 누락 안내를 표시한다.
- 일별 장기 집계에는 IP·User-Agent·세션 식별자를 남기지 않는다. 일별 salt와 SQL 만료는 기본 설정으로 적용한다. DB 논리 삭제와 로그 회전은 저장장치의 물리적 완전 삭제를 뜻하지 않는다. 별도 백업을 만들 경우 동일한 보관 기간을 관리한다.

편집기를 종료하면 그 프로세스가 열었던 SSH 터널도 닫는다. 터널이 열려 있는 동안에는 같은 맥의 다른 로컬 프로세스도 4390 포트에 접근할 수 있으므로 Umami 로그인은 유지한다. 인터넷용 관리자 도메인·공개 공유 URL·DB 포트는 만들지 않는다. 소유자 계정이나 서버 관리자까지 격리하는 구조는 아니다.

## 검증

`.local/editor-venv/bin/python -m unittest discover -s tests -p 'test_*.py'`로 수집 스키마, 개인정보 필드 거부, 브라우저 제외, 이벤트 중복 방지, 고정 전송 대상, 로컬 편집기 인증·CSRF, SSH 인자를 확인한다.

준비된 Linux 서버에서 `python3 tests/analytics_integration.py`로 별도 nginx와 임시 웹사이트를 사용해 실제 Umami 저장·요청 제한·IP 헤더 덮어쓰기·query/referrer 제거·관리 경로 404를 확인한다. 만료 SQL은 임시 자료를 넣은 트랜잭션에서 두 번 실행한 뒤 rollback한다. 시험용 웹사이트의 수집 자료는 마지막에 삭제한다. 운영 nginx와 홈페이지는 변경하지 않는다.
