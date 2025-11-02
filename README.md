# HomeShare

Tiny LAN share toy. Point it at a macOS folder, serve it at `http://IP:PORT`, and browse from the web UI or CLI. Read-only by default; add `--read-write` for resumable uploads and ad-hoc ZIP bundles. No auth—keep it on trusted Wi-Fi.

```bash
# Start the server (read-only example on port 9000)
python3 server.py --share-root /Users/you/Share --port 9000

# Optional: enable write access and allow overwrites
python3 server.py --share-root /Users/you/Share --port 9000 \
  --read-write --allow-overwrite

# CLI examples
python3 cli.py --url http://<LAN-IP>:9000 list
python3 cli.py --url http://<LAN-IP>:9000 download path/file.mp4 ./file.mp4
```

Web: browse, preview (plays if the browser supports the codec), multi-select zip.  
CLI: `upload` / `download` resume by default; `zip` bundles multiple paths.  
Players: nPlayer and other HTTP Range clients stream originals—no transcoding.  
Domain: hit `http://<MacHost>.local:PORT` via Bonjour on the same subnet.  
Tips: uploads buffer in `.homeshare_state/`; restarts resume cleanly. Only expose on trusted networks.
