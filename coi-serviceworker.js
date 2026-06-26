/* coi-serviceworker.js
 * 정적 호스팅(GitHub Pages 등)에서 SharedArrayBuffer 를 쓸 수 있도록
 * COOP/COEP 헤더를 자동으로 붙여 주는 작은 서비스 워커.
 *
 * 사용법: index.html 의 <head> 에서 가장 먼저 이 파일을 불러오면 끝.
 *   <script src="coi-serviceworker.js"></script>
 * 첫 접속 때 워커가 페이지를 한 번 새로고침하여 교차 출처 격리를 켭니다.
 */

if (typeof window === "undefined") {
  // ───── 서비스 워커 컨텍스트 ─────
  self.addEventListener("install", () => self.skipWaiting());
  self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

  self.addEventListener("fetch", (event) => {
    const req = event.request;
    // 캐시 전용 요청은 건드리지 않음
    if (req.cache === "only-if-cached" && req.mode !== "same-origin") return;

    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res.status === 0) return res; // opaque 응답은 그대로
          const headers = new Headers(res.headers);
          headers.set("Cross-Origin-Opener-Policy", "same-origin");
          headers.set("Cross-Origin-Embedder-Policy", "require-corp");
          // 교차 출처 리소스(Pyodide CDN)도 임베드 가능하도록 CORP 부여
          headers.set("Cross-Origin-Resource-Policy", "cross-origin");
          return new Response(res.body, {
            status: res.status,
            statusText: res.statusText,
            headers,
          });
        })
        .catch((e) => {
          console.error("[coi-sw] fetch 실패:", e);
          throw e;
        })
    );
  });
} else {
  // ───── 페이지 컨텍스트: 등록 + 필요 시 1회 새로고침 ─────
  (async () => {
    if (window.crossOriginIsolated) return;       // 이미 격리됨 → 할 일 없음
    if (!window.isSecureContext) return;           // https/localhost 가 아니면 불가
    if (!("serviceWorker" in navigator)) return;

    const src = document.currentScript && document.currentScript.src;
    try {
      const reg = await navigator.serviceWorker.register(src, { scope: "./" });
      // 아직 컨트롤러가 없으면, 워커가 활성화된 뒤 새로고침해야 헤더가 적용됨
      if (!navigator.serviceWorker.controller) {
        navigator.serviceWorker.addEventListener("controllerchange", () =>
          window.location.reload()
        );
        if (reg.active) window.location.reload();
      }
    } catch (e) {
      console.error("[coi-sw] 등록 실패:", e);
    }
  })();
}
