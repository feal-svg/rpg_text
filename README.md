# 검과 마법의 텍스트 RPG ― 브라우저 단독(Pyodide) 버전

서버(컴퓨터)가 필요 없습니다. **원본 `rpg_game.py` 를 한 줄도 고치지 않고**,
브라우저 안에서 파이썬을 그대로 돌립니다(Pyodide = 파이썬을 WebAssembly로).
한 번 호스팅해 두면 **휴대폰에서 주소만으로 접속해 단독 실행**되고, 첫 로딩 뒤엔 오프라인도 됩니다.

## 구성

```
rpg_pyodide/
├─ index.html             # 화면(UI). 원본 웹버전 UI를 그대로 사용
├─ worker.js              # Pyodide 로드 + 게임 구동 + 입력 다리(SharedArrayBuffer)
├─ rpg_game.py            # 원본 게임 (그대로)
├─ coi-serviceworker.js   # 정적 호스팅에서 SharedArrayBuffer 를 켜는 서비스 워커
├─ serve.py               # 로컬 테스트용 서버(헤더 포함)
└─ .nojekyll              # GitHub Pages가 파일을 그대로 서빙하도록
```

## 작동 원리 (서버 버전과 동일한 발상)

게임의 입출력 통로만 갈아끼웁니다. `print` 출력은 모았다가, 게임이 입력(`ask`)을
요청하는 순간 **한 화면**으로 확정해 보여 줍니다. 다른 점은 입력을 받는 방법뿐입니다 —
서버 버전은 큐로, 이 버전은 `SharedArrayBuffer` 위에서 게임 스레드를 잠재웠다 깨우며
브라우저 입력을 동기적으로 전달합니다. 그래서 `input()` 을 쓰는 원본 코드를
**고치지 않고** 그대로 돌릴 수 있습니다.

## 휴대폰에서 단독 실행하기 — GitHub Pages (권장)

1. GitHub에 새 저장소를 만들고 이 폴더의 파일을 모두 올립니다.
2. 저장소 **Settings → Pages → Build and deployment** 에서
   Source 를 `Deploy from a branch`, 브랜치를 `main` / 폴더 `/ (root)` 로 지정하고 저장.
3. 잠시 뒤 생성되는 주소(`https://<아이디>.github.io/<저장소>/`)를 휴대폰 브라우저로 엽니다.
   - 첫 접속 때 화면이 한 번 새로고침됩니다(서비스 워커가 격리 헤더를 켜는 과정).
   - 그다음 "파이썬 엔진 다운로드"가 한 번 진행됩니다(수십 MB, 잠깐 걸림). 이후엔 빠릅니다.

`https` 로 제공되기 때문에 `coi-serviceworker.js` 가 `SharedArrayBuffer` 사용에
필요한 교차 출처 격리를 자동으로 켭니다.

## 컴퓨터에서 먼저 확인하기 (로컬)

```bash
cd rpg_pyodide
python serve.py        # 안 되면 python3 serve.py
```

브라우저(**크롬 권장**)에서 `http://localhost:8000` 접속.

> 참고: 휴대폰에서 `http://컴퓨터IP:8000` 으로 붙는 방식은 동작하지 않습니다.
> `SharedArrayBuffer` 는 보안 컨텍스트(`https` 또는 `localhost`)를 요구하기 때문입니다.
> 휴대폰 단독 실행은 위의 GitHub Pages 방식을 쓰세요.
> 파일을 더블클릭해 `file://` 로 여는 것도 동작하지 않습니다(브라우저 보안 정책).

## 세이브

게임의 세이브는 브라우저의 `localStorage` 에 저장됩니다. 같은 기기·같은 브라우저로
다시 접속하면 이어집니다. 상단 **새 게임** 버튼은 저장 기록을 지우고 새로 시작합니다.
