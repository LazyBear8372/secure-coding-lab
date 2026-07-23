# AGENTS.md

## Project Overview

- 본 프로젝트는 사용자가 중고 상품을 등록하고 다른 사용자와 거래할 수 있는 웹 기반 중고거래 플랫폼이다.

### Function

- 회원 관리
	- 회원가입
	- 로그인
	- 로그아웃
	- 사용자 프로필 조회
	- 소개글·비밀번호 수정
	- 회원 탈퇴 및 세션 폐기

- 상품 관리
	- 상품명, 설명, 가격, 대표 사진을 포함한 상품 등록
	- 전체 상품 목록 조회 및 검색
	- 목록에는 상품명을 표시하고 상세 페이지에는 전체 상품 정보 표시
	- 판매자가 자신의 상품 수정
	- 판매자가 자신의 상품 삭제
	- 판매자가 자신이 등록한 상품 목록 조회
	- 판매자가 자신의 상품 판매 완료로 전환 가능
	- 판매 중, 판매 완료 상품 표시

- 채팅
	- 전체 채팅방 입장 및 메시지 전송
	- 상품을 통한 판매자–구매자 1:1 채팅방 생성
	- 기존 1:1 채팅방 입장 및 메시지 전송

- 지갑
	- 모의 입금
	- 모의 출금
	- 잔액, 송금 내역 조회

- 신고 및 관리자 처리
	- 사용자 신고
	- 상품 신고
	- 신고 누적에 따른 상품, 유저 자동 차단
	- 관리자의 신고 승인, 거절
	- 관리자의 수동 휴면·차단·복구

- 송금
	- 채팅창 내에서 구매자가 판매자에게 송금

- 관리자 페이지
	- 유저 관리 페이지를 통해 유저 차단, 차단 해제 가능
	- 신고 내역 페이지를 통해 신고 내역 확인 및 유저 차단 가능
	- 제품 목록 페이지를 통해 제품 임의 삭제 가능

## Tech Stack

웹 서버			Nginx
WAS 			Uvicorn
프레임워크		FastAPI
ORM				SQLAlchemy (2.0 스타일)
DB				PostgreSQL (Docker로 실행)
프론트엔드		Jinja2 + HTMX
패키지 관리		uv
테스트			pytest
개발 환경		Docker Compose

## Architecture

### Database Schema

- users
	- id: UUID, PK
	- username: VARCHAR, UNIQUE
	- password_hash: VARCHAR
	- status: active / suspended / withdrawn
	- created_at, updated_at, suspended_at, withdrawn_at
	- bio: TEXT
	- role: user / admin

- sessions
	- id: UUID, PK
	- user_id: FK → users
	- token_hash: VARCHAR, UNIQUE
	- expires_at, revoked_at, created_at

- products
	- id: UUID, PK
	- seller_id: FK → users
	- name: VARCHAR
	- description: TEXT
	- price: BIGINT
	- image_key: VARCHAR
	- status: active / sold / blocked / deleted
	- created_at, updated_at, blocked_at, deleted_at
	- CHECK(price >= 0)

- chat_rooms
	- id: UUID, PK
	- type: global / product
	- product_id: FK → products, NULL 허용
	- created_at
	- CHECK: global이면 product_id는 NULL
	- CHECK: product이면 product_id는 반드시 존재

- chat_room_members
	- chat_room_id: FK → chat_rooms
	- user_id: FK → users
	- joined_at
	- last_read_at
	- PK(chat_room_id, user_id)

- chat_messages
	- id: UUID, PK
	- chat_room_id: FK → chat_rooms
	- sender_id: FK → users
	- content: TEXT
	- created_at

- reports
	- id: UUID, PK
	- reporter_id: FK → users
	- target_user_id: FK → users, NULL 허용
	- target_product_id: FK → products, NULL 허용
	- reason: TEXT
	- status: pending / accepted / rejected
	- created_at
	- reviewed_at
	- CHECK: target_user_id와 target_product_id 중 정확히 하나만 존재
	- PARTIAL UNIQUE(reporter_id, target_user_id): target_user_id가 존재하고 status가 pending 또는 accepted인 경우
	- PARTIAL UNIQUE(reporter_id, target_product_id): target_product_id가 존재하고 status가 pending 또는 accepted인 경우

- wallets
	- id: UUID, PK
	- user_id: FK → users, UNIQUE
	- balance: BIGINT
	- created_at, updated_at
	- CHECK(balance >= 0)

- wallet_transfers
	- id: UUID, PK
	- chat_room_id: FK → chat_rooms, NULL 허용
	- sender_wallet_id: FK → wallets, NULL 허용
	- receiver_wallet_id: FK → wallets, NULL 허용
	- amount: BIGINT
	- type: deposit / withdrawal / transfer
	- idempotency_key: UUID, UNIQUE, NOT NULL
	- created_at
	- CHECK(amount > 0)
	- CHECK: deposit이면 chat_room_id와 sender_wallet_id는 NULL이고 receiver_wallet_id는 존재
	- CHECK: withdrawal이면 chat_room_id와 receiver_wallet_id는 NULL이고 sender_wallet_id는 존재
	- CHECK: transfer이면 chat_room_id, sender_wallet_id, receiver_wallet_id가 모두 존재
	- CHECK: sender_wallet_id와 receiver_wallet_id가 모두 존재하면 서로 다른 지갑이어야 함

### Directory Structure

## Git Convention

GitHub Flow: Issue 생성 → 브랜치 분리 → 작업/커밋 → PR로 `main` 병합. PR 본문에
`Closes #이슈번호`를 작성하여 병합 시 Issue를 자동으로 닫는다.

Branch Naming: `<타입>/<이슈번호>-<짧은-설명>` (이슈와 타입 같게)

- 예: `feat/12-product-create`, `fix/15-login-error`

Commit Convention: `<타입>: <설명>`

- `feat`: 새 기능 추가
- `fix`: 버그 수정
- `docs`: 문서 변경
- `refactor`: 기능 변화 없는 구조 개선
- `chore`: 빌드·설정·패키지 등 기타 작업
- `security`: 취약점 수정, 보안 설정 강화, 인증·인가 개선 등 보안 목적의 변경
- `test`: 기능 변경 없이 테스트 코드 추가 또는 기존 테스트 개선

## Agile Development

- 기능을 작은 단위로 나누어 짧은 스프린트로 개발한다.
- 각 스프린트에서는 요구사항 정의, 설계, 구현, 테스트, 보안 검토를 수행한다.
- 테스트와 보안 검증을 통과해야 기능이 완료된 것으로 본다.
- 반복이 끝나면 결과와 개선점을 검토하여 다음 작업에 반영한다.

### Backlog Management

- GitHub Issues를 백로그로 사용한다.
- 새 요구사항과 추가 작업은 Issue로 등록하고 우선순위를 정한다.

### Issue Priority

- 모든 Issue에는 다음 우선순위 Label 중 하나를 지정한다.
	- `priority: high`: 핵심 기능, 심각한 보안 문제, 다른 작업을 차단하는 작업
	- `priority: medium`: 중요하지만 즉시 처리할 필요가 없는 기능과 개선
	- `priority: low`: 편의 기능, 사소한 개선, 장기적으로 검토할 작업

- 스프린트 계획 시 우선순위, 보안 위험, 작업 의존성, 스프린트 목표를 고려하여 Issue를
선택한다.

### Milestone(Sprint) Management

- 스프린트마다 하나의 Milestone을 생성한다.
- 스프린트 시작 전에 처리할 Issue를 선택하여 Milestone에 배정한다.
- 새로 발견된 추가 작업은 Issue로 등록하고 다음 스프린트에서 우선순위를 검토한다.
- 완료하지 못한 Issue는 닫지 않고 백로그로 돌려보낸 뒤 우선순위를 재검토한다.
- 모든 Issue를 완료하거나 재배정하고 스프린트 검토가 끝나면 Milestone을 종료한다.
