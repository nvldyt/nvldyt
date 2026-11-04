# Private Ephemeral Chat — security branch

This branch adds a separate FastAPI implementation under `secure_app/`. It does not overwrite the original chat implementation.

## Security model

- Browser-side AES-256-GCM encryption; the server relays opaque ciphertext only.
- Key material is derived in the browser using PBKDF2-HMAC-SHA-256 (600,000 iterations) from a high-entropy room secret and room ID.
- Server keeps only a verifier and active WebSocket objects in process memory.
- No message database, message history, localStorage, or server-side message logging.
- Maximum two active participants per room.
- A room is destroyed when the last participant disconnects; idle/maximum lifetime limits are also applied.
- HTTPS/WSS must be enabled by the hosting platform.
- Same-origin WebSocket Origin check by default; configure `ALLOWED_ORIGINS` only if needed.

## Important limitations

This is a security-oriented prototype, not an independently audited secure messenger. The verifier is a bearer proof held in memory; anyone who obtains the room code can join. Share it only through a separate trusted channel. The server serves the browser JavaScript, so a malicious or compromised hosting operator could serve modified JavaScript that steals the room secret. For a stronger threat model, independently host/sign the client or distribute an audited native client. No implementation can promise "maximum security" without a defined threat model and independent review.

The in-memory room registry is process-local. A restart/redeploy clears rooms and disconnects everyone. Do not run multiple workers/instances without redesigning ephemeral room coordination; this deployment intentionally uses one process.

## Local run

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn secure_app.main:app --reload
```

Open http://127.0.0.1:8000. Use HTTPS in production.

## Render deployment

1. Connect this GitHub repository to Render.
2. Select the branch `security/e2ee-temporary-chat`.
3. Render detects `render.yaml`; review the service settings.
4. Deploy. Use the generated HTTPS URL.
5. Test with two separate browsers/devices. Create a room in one and share the room code privately with the other.
6. Confirm that messages cannot be read in browser DevTools Network frames or server logs (only encrypted envelopes should appear).

## Operational requirements before real sensitive use

- Add a custom domain and verify HTTPS/WSS.
- Keep repository and deployment secrets out of source control.
- Configure a restrictive `ALLOWED_ORIGINS` if using a proxy/domain arrangement where Host and Origin differ.
- Add automated tests, dependency pinning, abuse controls, and independent cryptographic/security review.
- Do not treat GitHub or hosting-provider access as end-to-end private: administrators of the hosting account can change deployed code.
