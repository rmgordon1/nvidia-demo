import http.server, sys, time, urllib.parse
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        u = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(u.query)
        if u.path == "/sleep":
            time.sleep(float(q.get("s", ["8"])[0])); self.send_response(200); self.send_header("Content-Type", "image/gif"); self.end_headers()
            self.wfile.write(b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"); return
        view = q.get("v", ["itsm"])[0]; s = q.get("s", ["8"])[0]
        src = f"http://localhost:8080/e2e.html?v={view}&role={q.get('role', ['admin'])[0]}" if q.get("e2e") else f"http://localhost:8080/#/{view}"
        page = f'<html><body style="margin:0"><iframe src="{src}" style="border:0;width:1440px;height:1500px"></iframe><img src="/sleep?s={s}" style="display:none"></body></html>'
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(page.encode())
http.server.ThreadingHTTPServer(("127.0.0.1", 8099), H).serve_forever()
