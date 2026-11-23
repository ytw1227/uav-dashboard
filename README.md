# UAV 통신 연구 대시보드

군집 UAV 비행 기록에서 수색망의 연결 상태, 단절 시점, 중계 드론의 이동, 재연결 과정을 살펴보는 NiceGUI 대시보드입니다. `FINAL-V`는 대시보드 코드를 팀 시뮬레이터에서 분리한 첫 기준점입니다.

## Windows에서 열기

Python 3.12를 설치한 뒤 저장소 폴더에서 한 번 실행합니다.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r nicegui_dashboard/requirements.txt
```

팀에서 받은 재생 자료는 아래 이름으로 둡니다. GitHub에서는 대시보드 코드와 검증 도구를 배포하며, 전체 비행 녹화본은 별도로 전달합니다.

| 로컬 위치 | 표시되는 자료 |
| --- | --- |
| `data/UAV_대시보드_v2.html` | 전체 70개 지역의 기본 대형·재배치 적용 비행 140건 |
| `data/상암DMC_대시보드.html` | 상암 최신 공유본의 두 비행과 사건 상세 |
| `data/results/` | 완료된 실험이 등록되는 로컬 보관소. 처음 실행할 때 빈 보관소로 시작할 수 있습니다. |

`Start-Dashboard.cmd`를 실행하고 브라우저에서 [전체 지역](http://127.0.0.1:8092/regions)을 엽니다. 창에서 `Ctrl+C`를 누르면 종료됩니다. 8092에서 기존 대시보드를 쓰고 있다면 `.\Start-Dashboard.cmd --port 8196`을 실행하고 [8196 전체 지역](http://127.0.0.1:8196/regions)을 엽니다. 자료가 다른 위치에 있으면 `settings.example.json`을 `settings.local.json`으로 복사해 경로를 지정합니다. 같은 이름의 환경 변수가 있으면 그 값을 우선합니다.

## 기록을 보는 순서

1. **전체 지역**에서 지역과 비행 방식을 고르고 비행을 재생합니다. 상암 DMC는 지역 목록에서 선택할 수 있습니다. 상암 재배치 적용 기록의 사건 13은 2,845.5초에 시작해 2,889초에 끝납니다.
2. **사건 상세**에서 시각을 옮기며 기체 위치·링크·통신 여유·단절과 재연결을 같은 시각에 비교합니다. 각 공유본은 자료 식별값과 변환본을 확인해 읽습니다.
3. **로컬 결과**는 이 컴퓨터에 등록된 완료 기록을 보여 줍니다. 팀에서 같은 형식으로 내보낸 결과 폴더가 있으면 아래 명령으로 등록합니다. 경로를 실제 결과 폴더로 바꿔 실행하세요.

```powershell
.\.venv\Scripts\python.exe -m nicegui_dashboard.ingest publish "D:\flight-exports\run\revision" --output-root .\data\results
```

기본 결과 서비스는 약 10초마다 보관소를 확인합니다. 같은 실행·변환본을 다시 등록해도 목록에는 한 건으로 남습니다. 내보낸 결과 등록과 새 비행 계산은 별도 작업입니다.

## 팀 시뮬레이터가 바뀌면

**전체 지역 화면 상단의 `팀 코드 갱신`을 누릅니다.** 버튼 옆에 연결된 팀 코드 버전과 갱신 결과가 표시됩니다. 보고 있던 지역과 비행 기록은 계속 열려 있습니다. Windows에서는 `Sync-Team-Code.cmd`(바탕화면 바로가기 `팀 코드 갱신`)로도 같은 작업을 할 수 있습니다.

첫 실행은 대시보드 폴더 옆의 `team-simulator`에 팀 코드를 내려받습니다. 다음부터는 같은 폴더를 팀 저장소의 `main`과 맞춥니다. 로컬에서 파일을 바꾸거나 별도로 커밋한 경우에는 중단하고 이유를 표시합니다.

연결한 코드는 대시보드에서 `.\.venv\Scripts\python.exe -m nicegui_dashboard team-version`으로도 확인할 수 있습니다. 이 단계는 소스 코드 갱신까지입니다. 기존 비행 기록은 해당 시뮬레이션을 실행했을 때의 데이터를 보여 줍니다.

현재 보관된 재생 자료는 재배치 판단 주기가 3초인 실험에서 생성됐습니다. 팀 코드의 판단 주기나 모델이 바뀌어도 과거 비행 수치는 그 실행의 기록으로 유지됩니다. 새 버전을 반영할 때는 팀 커밋을 기록하고, 먼저 한 지역의 기본·적용 비행을 같은 조건으로 내보낸 뒤 `로컬 결과`에서 지도, 사건 시각, 지표 정의를 대조합니다. 호환성을 확인한 뒤 나머지 지역을 갱신합니다. [팀 시뮬레이터 저장소](https://github.com/MinchanKim1215/capstone-final-v1)

## 코드와 확인 방법

| 폴더 | 역할 |
| --- | --- |
| `nicegui_dashboard/` | 화면 진입점, 지도·사건 재생, 비교, 보관소 연동 |
| `nicegui_dashboard/vendor/` | 기준 공유본을 읽고 재생하는 데 쓰는 고정된 화면 자원과 보조 데이터 |
| `result_service/` | 결과 형식 검사, 등록, 조회, 내보내기 |
| `nicegui_dashboard/tests/` | 자료 읽기, 등록, 비교, 상태 동기화와 실제 브라우저 화면 확인 |

위의 두 재생 자료를 준비한 뒤 다음 명령으로 테스트합니다.

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s nicegui_dashboard/tests -v
```

브라우저 검증에는 Node.js와 Chrome이 필요합니다. 대시보드를 실행한 상태에서 아래 명령을 사용합니다. 이 검증은 전체 지역 목록, 로컬 서비스 연결, 상암 사건 상세를 실제로 엽니다.

```powershell
npm --prefix nicegui_dashboard/tests install
npm --prefix nicegui_dashboard/tests run smoke -- http://127.0.0.1:8092
```
