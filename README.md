# Secure Coding Lab

FastAPI 기반 중고거래 플랫폼 프로젝트입니다.

## 현재 구현 범위

- 전체 도메인 SQLAlchemy 모델과 Alembic 초기 마이그레이션
- 회원가입, 로그인, 로그아웃과 DB 세션 관리
- 공개 프로필, 소개글·비밀번호 변경, 회원 탈퇴
- Argon2id 비밀번호 해시, CSRF 방어, 안전한 세션 쿠키

## 기술 스택

- Nginx, Uvicorn, FastAPI
- SQLAlchemy 2.0, Alembic, PostgreSQL
- Jinja2, HTMX
- uv, pytest, Ruff
- Docker Compose

## 로컬 실행

Python 3.13과 [uv](https://docs.astral.sh/uv/)가 필요합니다.

```bash
cp .env.example .env
uv sync --dev
uv run uvicorn secure_coding_lab.main:app --reload
```

웹 애플리케이션은 `http://127.0.0.1:8000`, API 문서는
`http://127.0.0.1:8000/docs`에서 확인할 수 있습니다.

## Docker Compose 실행

```bash
cp .env.example .env
docker compose up --build -d
docker compose ps
```

Nginx를 통한 서비스 주소는 `http://127.0.0.1:8000`입니다.

```bash
docker compose logs -f
docker compose down
```

## 검증

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
docker compose config --quiet
```

테스트에는 데이터베이스 무결성 제약, 인증 실패, 세션 폐기, CSRF, 저장형 XSS,
비밀번호 변경 및 탈퇴 흐름이 포함됩니다.

## 환경 변수

실제 비밀값이 담긴 `.env`는 Git에 커밋하지 않습니다. 새 환경에서는
`.env.example`을 복사한 뒤 `SECRET_KEY`와 `POSTGRES_PASSWORD`를 변경해야 합니다.
`SESSION_TTL_HOURS`로 로그인 세션의 유효 시간을 조정할 수 있습니다.
