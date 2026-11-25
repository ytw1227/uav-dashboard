# Render 공유 서버

2026-11-25 최신 프로젝트의 표시 대상 50개 지역과 포함된 분당 실행 결과를 공유합니다.
기존 기록의 재생·비교·평가 기능을 사용하며, 연구 코드 갱신과 새 계산은 조회 모드에서 비활성화합니다.

## 서비스 설정

- 서비스 종류: Web Service
- 런타임: Python 3.12.14
- 요금제: Free
- 리전: Singapore
- 빌드 명령: `python tools/render_data.py && pip install -r requirements.txt`
- 시작 명령: `python -B prepare_sample.py && python -B -m nicegui_dashboard app --host 0.0.0.0 --read-only`
- 포트: Render의 `PORT` 환경변수 자동 사용
- 상태 확인: `/healthz`

`deploy_data/manifest.json`과 분할 파일은 실행 자료의 압축본입니다. 빌드에서 각 부분과 복원된 파일의 SHA-256을 검사해 `data/`에 복원합니다. 원본 HTML의 단일 파일 크기 때문에 일반 Git에 그대로 넣지 않고 분할 보관합니다.

시작할 때 `prepare_sample.py`가 포함된 완료 기록을 등록합니다. 무료 서버가 다시 시작해 임시 파일이 초기화되어도 배포 자료에서 등록 상태를 다시 만들 수 있습니다. 실행 중 업로드하거나 새로 계산한 자료의 영구 보관은 이 구성에 포함되지 않습니다.

## 이번 공유본

- 화면 및 배포용 HTML: 선택한 50개 지역, 기존 기본 대형/적용 비행 100개
- 포함된 추가 결과: 분당 적용/기본 대형과 관련 평가·운용 기록
- 원본 결과값 및 보존한 비행 데이터는 변경하지 않았습니다.
- Python에서 큰 HTML을 읽을 때 사용이 끝난 파일 메모리를 회수할 수 있도록 처리했습니다.
- 기존 로컬 실행은 `--read-only`를 지정하지 않으면 계속 사용할 수 있습니다.

## 업데이트

같은 서비스에 새 배포를 적용하면 기존 공유 주소를 계속 사용합니다.

Git에서 새로 받은 폴더에는 원본 `source/` 폴더가 없으므로, 앱을 실행하기 전에 기존 배포 자료를 먼저 복원합니다.

```sh
python tools/render_data.py
```

복원된 `data/` 안의 공유할 자료를 수정한 뒤, 파일 목록과 실제 자료의 위치를 명시해 다시 묶습니다.

```sh
python tools/render_data.py --pack --source-data data --data-dir data
```

`--source-data`는 포함할 파일 목록을 정하는 옵션입니다. 이 절차는 앱을 실행하지 않은 깨끗한 작업 폴더에서 수행하고, 공유 대상이 아닌 실행 로그·캐시를 `data/`에 넣지 않습니다. 공유 HTML을 변경했다면 코드에 고정된 해당 자료의 SHA-256도 새 파일과 일치시켜야 합니다. 빌드 서버의 일반 복원 명령에는 원본 `source/` 폴더가 필요하지 않습니다.

무료 서비스의 휴면·메모리·전송량 제한은 Render 정책을 따릅니다. 공식 문서: https://render.com/docs/free
