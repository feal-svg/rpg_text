# -*- coding: utf-8 -*-
"""
로컬 테스트용 정적 서버 (교차 출처 격리 헤더 포함)
====================================================================
SharedArrayBuffer 를 쓰려면 페이지가 COOP/COEP 헤더와 함께 제공되어야 합니다.
이 서버는 그 헤더를 붙여 줍니다. (브라우저는 크롬 권장)

실행:
    python serve.py            (안 되면)  python3 serve.py
그다음 브라우저에서:
    http://localhost:8000

※ 휴대폰에서 'http://컴퓨터IP:8000' 으로 접속하는 방식은
  SharedArrayBuffer 가 보안 컨텍스트(https/localhost)를 요구하기 때문에 동작하지 않습니다.
  휴대폰 단독 실행은 README 의 'GitHub Pages 배포'를 따르세요.
"""
import http.server
import socketserver

PORT = 8000


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        # credentialless: Pyodide CDN 같은 교차 출처 리소스를 CORP 없이도 불러올 수 있음(크롬)
        self.send_header("Cross-Origin-Embedder-Policy", "credentialless")
        self.send_header("Cross-Origin-Resource-Policy", "cross-origin")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("127.0.0.1", PORT), Handler) as httpd:
        print("=" * 56)
        print("  로컬 테스트 서버 시작 (크롬에서 열어 주세요)")
        print(f"      http://localhost:{PORT}")
        print("  종료: Ctrl + C")
        print("=" * 56)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n서버를 종료합니다.")
