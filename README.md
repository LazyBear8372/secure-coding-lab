# Secure Coding Lab 실행 방법

간단히 docker compose만을 사용해서 실행할 수 있다.

## 실행

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
