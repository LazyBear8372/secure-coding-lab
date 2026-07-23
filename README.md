# Secure Coding Lab 실행 방법

## 실행

```bash
git clone https://github.com/LazyBear8372/secure-coding-lab.git
cd secure-coding-lab
cp .env.example .env
```

Windows PowerShell에서는 마지막 명령 대신 다음을 실행한다.

```powershell
Copy-Item .env.example .env
```

`.env`를 열어 `SECRET_KEY`와 `POSTGRES_PASSWORD`를 임의의 안전한 값으로 변경한다.
두 값에는 영문자와 숫자만 사용하고, `SECRET_KEY`는 32자 이상으로 설정한다.

```bash
docker compose up --build -d
docker compose ps
```

`db`, `app`, `nginx`가 모두 `healthy`가 되면 브라우저에서 아래 주소로 접속한다.

<http://127.0.0.1:8000>


## 종료

```bash
docker compose down
```

DB와 업로드 이미지를 포함한 모든 데이터를 삭제하려면 다음을 실행한다.

```bash
docker compose down -v
```
