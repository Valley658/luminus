# Luminus

스텔라이브 영상·Shorts와 팬 커뮤니티를 위한 웹 애플리케이션입니다.

## 구성

- `drafts/index2.html`: 현재 메인 화면 원본
- `frontend/luminus/assets`: JavaScript/CSS 원본
- `templates/luminus`, `static/luminus`: 배포용 템플릿·정적 파일
- `scripts`: Python 서버, 계정·관리자·영상·검색 기능과 테스트
- `rust-server`: 보존된 Rust 서버 소스
- `templates`의 기타 파일: 보존된 과거 화면
- `tools`: JavaScript/WASM 빌드 도구 (`npm ci`로 의존성 설치)

## 로컬 실행

Windows와 Python을 준비한 뒤 다음 명령을 실행합니다.

```powershell
python -m pip install -r scripts/requirements.txt
Copy-Item .env.example .env
# .env에 필요한 값을 직접 입력합니다.
python scripts/prepare-layout.py
python scripts/server.py
```

로컬 서버는 `127.0.0.1:80`에서만 실행합니다. YouTube·Discord·Drive 기능에는 각각 실제 API/OAuth 설정이 필요합니다. `SECRET_KEY`는 예제의 명령으로 생성해 `.env`에 보관합니다. 운영 VM은 Nginx `127.0.0.1:80`, Python `127.0.0.1:8081`, MySQL 8.0을 사용합니다. 운영 DB 전환에는 별도의 마이그레이션과 활성화 절차가 필요합니다.

## 테스트

```powershell
python -m unittest discover -s scripts -p "test_*.py"
```

## 저장소에서 제외되는 파일

`.env`, 비밀키, DB 및 백업, 사용자 업로드, 로그, VM 이미지, 다운로드한 실행 파일, `node_modules`, 실행 상태 파일과 실제 서버 접속 설정은 포함하지 않습니다. 서버 데이터는 별도로 백업해야 합니다. 이 저장소를 배포할 때 운영 서버의 `data/`와 `DB/`를 덮어쓰지 마세요.

과거 코드와 현재 코드가 함께 보존되어 있습니다. 현재 Python 서버의 `legacy_ui.py`는 활성화하지 않습니다. 외부 라이브러리·이미지·폰트의 권리는 각 권리자에게 있습니다.
