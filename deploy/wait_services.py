"""Wait at most 60 seconds for local bridge/RSSHub listeners, not source success."""
import socket,time

deadline=time.monotonic()+60
while time.monotonic()<deadline:
    ready=[]
    for port in (8765,1200):
        with socket.socket() as sock:
            sock.settimeout(.5);ready.append(sock.connect_ex(("127.0.0.1",port))==0)
    if all(ready):
        print("Local bridge and RSSHub listeners ready; individual source results are checked by the collector.")
        break
    time.sleep(.5)
else:raise SystemExit("Local bridge/RSSHub startup timed out; no source success claimed")
