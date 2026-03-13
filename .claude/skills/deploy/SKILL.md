---
name: deploy
description: Deploy to VPS — push, pull, restart, verify
disable-model-invocation: true
---

Deploy the current branch to the VPS. One SSH attempt only (fail2ban).

## Pre-flight

1. Check working tree is clean:
   ```
   cd /Users/chemisttt/Desktop/code/onchain-radar && git status --porcelain
   ```
   If dirty, STOP and report uncommitted changes.

2. Push to remote:
   ```
   cd /Users/chemisttt/Desktop/code/onchain-radar && git push
   ```
   If push fails, STOP and report.

## Deploy

3. Single SSH command — pull + kill zombies + restart:
   ```
   ssh botuser@77.221.154.136 "cd ~/onchain-radar && git pull && pkill -f 'uvicorn main:app' || true && sleep 1 && sudo systemctl restart onchain-radar"
   ```

## Verify

4. Check service status:
   ```
   ssh botuser@77.221.154.136 "sudo systemctl status onchain-radar"
   ```

5. Health check (wait 3s for startup):
   ```
   sleep 3 && ssh botuser@77.221.154.136 "curl -s http://127.0.0.1:8000/api/health"
   ```

## Report

Output format:
```
## Deploy Result

- Push: OK / FAIL
- Pull: OK / FAIL
- Restart: OK / FAIL
- Health: OK / FAIL
- Status: <running / failed>
```

If any step FAILS, stop and report the error. Do NOT retry SSH (fail2ban risk).
