/* worker.js ─ 브라우저 안에서 파이썬 게임을 돌리는 워커
 *
 * 원리(서버 버전과 동일):
 *   - 게임의 print 출력은 파이썬 쪽 _pieces 에 모은다
 *   - 게임이 입력(ask)을 요청하면 → 모인 출력을 '한 화면'으로 메인 스레드에 보내고,
 *     SharedArrayBuffer 위에서 Atomics.wait 로 블로킹하며 입력을 기다린다
 *   - 메인 스레드(UI)가 SAB 에 입력을 써넣고 세대 카운터를 올리면 → 깨어나 값을 읽고 진행
 *   - 세이브(save_game)는 메인 스레드로 보내 localStorage 에 저장(워커엔 localStorage 가 없음)
 */

// 사용할 Pyodide 버전 (필요하면 이 줄만 바꾸세요)
const PYODIDE_VERSION = "0.26.4";
const PYODIDE_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/pyodide.js`;

let CTRL = null, DATA = null;
const DEC = new TextDecoder();

// ── 파이썬이 호출하는 블로킹 입력 함수: 화면을 보내고, 입력이 올 때까지 멈춘다 ──
self.bridge_ask = function (screen, prompt) {
  const g = Atomics.load(CTRL, 0);                 // 현재 세대
  self.postMessage({ type: "screen", screen: screen, prompt: prompt, ended: false });
  // UI 가 세대 카운터를 올릴 때까지 대기 (값이 g 인 동안만 잠듦)
  while (Atomics.load(CTRL, 0) === g) {
    Atomics.wait(CTRL, 0, g);
  }
  const len = Atomics.load(CTRL, 1);
  return DEC.decode(DATA.subarray(0, len));
};
self.bridge_save = function (text) {
  self.postMessage({ type: "save", data: text });
};
self.bridge_end = function (screen) {
  self.postMessage({ type: "screen", screen: screen, prompt: "", ended: true });
};

// 게임 로직은 한 줄도 고치지 않는다. 입출력 통로만 파이썬 쪽에서 갈아끼운다.
const BRIDGE_PY = `
import rpg_game as game
import js, time as _time

_pieces = []

def _emit(*args, **kwargs):
    sep = kwargs.get("sep", " ")
    end = kwargs.get("end", "\\n")
    _pieces.append(sep.join(str(a) for a in args) + end)

def _emit_slow(text, color="", delay=0.0):
    _emit(color + str(text) + game.C.RESET)

def _ask(prompt):
    screen = "".join(_pieces)
    _pieces.clear()
    return js.bridge_ask(screen, str(prompt))

class _TimeShim:
    def __getattr__(self, name): return getattr(_time, name)
    def sleep(self, *a, **k): return None

game.print = _emit
game.ask   = _ask
game.slow  = _emit_slow
game.time  = _TimeShim()
game.SAVE_FILE = "/rpg_save.json"

# 세이브를 localStorage 로 흘려보내기 위해 save_game 을 감싼다(본문은 원본 그대로 실행)
_orig_save = game.save_game
def _save_and_sync(p):
    _orig_save(p)
    try:
        with open(game.SAVE_FILE, "r", encoding="utf-8") as f:
            js.bridge_save(f.read())
    except Exception:
        pass
game.save_game = _save_and_sync

def _run():
    try:
        game.main()
    except Exception as e:
        _emit(game.C.RED + "\\n[오류] 게임이 중단되었습니다: " + str(e) + game.C.RESET)
    js.bridge_end("".join(_pieces))

_run()
`;

self.onmessage = async (e) => {
  const m = e.data;
  if (m.type !== "init") return;

  CTRL = new Int32Array(m.sab, 0, 2);
  DATA = new Uint8Array(m.sab, 8, m.dataLen);

  try {
    self.postMessage({ type: "loading", text: "엔진 다운로드 중…" });
    importScripts(PYODIDE_URL);
    const pyodide = await loadPyodide();

    self.postMessage({ type: "loading", text: "게임 불러오는 중…" });
    const src = await (await fetch("rpg_game.py")).text();
    pyodide.FS.writeFile("rpg_game.py", src);

    // 이전 세이브가 있으면 가상 파일로 복원 → 원본 load_game 이 그대로 읽음
    if (m.saveData) {
      try { pyodide.FS.writeFile("/rpg_save.json", m.saveData); } catch (_) {}
    }

    self.postMessage({ type: "ready" });
    // 게임 시작. ask() 에서 Atomics.wait 로 멈췄다 깨었다 하며 끝까지 진행한다.
    pyodide.runPython(BRIDGE_PY);
  } catch (err) {
    self.postMessage({ type: "error", message: String(err && err.message || err) });
  }
};
