# Home Assistant Setup Guide

## Step 1 — Find your actual entity IDs

The code assumes `switch.sprinkler_zone_1`, `switch.sprinkler_zone_2`, `switch.sprinkler_zone_3`.
These may differ depending on how your ZEN16s were named when added to SmartThings / HA.

To find them:
1. Open HA → **Settings → Devices & Services → Entities**
2. Search for "zen16" or "zooz" or "relay"
3. Note the entity IDs for ZEN16 #1's three relays
4. Cross-reference with your wiring:
   - Relay 1 → Zone 2 (front lawn right)
   - Relay 2 → Zone 1 (front beds/trees)
   - Relay 3 → Zone 3 (front lawn left)
5. Update `config.py` → `ZONES` → `entity_id` for zones 1, 2, 3
6. Update entity IDs in `ha_config/automations.yaml` to match

Common ZEN16 entity ID patterns in HA:
  - `switch.zooz_zen16_multirelay_relay_1`
  - `switch.zen16_relay_1`
  - `switch.garage_zen16_relay_1`  (if you named the device)

---

## Step 2 — Get a Long-Lived Access Token

1. In HA, click your profile icon (bottom left)
2. Scroll down → **Long-lived access tokens** → Create token
3. Give it a name like "sprinkler-agent"
4. Copy the token immediately (shown once only)
5. Put it in `.env` as `HA_TOKEN=`

---

## Step 3 — Add helpers.yaml

Option A — if you use packages or separate YAML files:
  Copy `helpers.yaml` into your HA config directory and add to `configuration.yaml`:
  ```yaml
  input_number: !include ha_config/helpers.yaml
  ```

Option B — paste directly into the HA UI:
  Settings → Devices & Services → Helpers → + Create helper → Number
  Create one for each zone using the values in helpers.yaml.

---

## Step 4 — Add automations

Option A — paste into automations.yaml (if you use YAML mode)

Option B — import via HA UI:
  Settings → Automations → ⋮ → Edit in YAML → paste each automation block

---

## Step 5 — Find your HA IP

Your HA NUC on Windows 11:
  - Open HA → Settings → System → Network
  - Note the IP (e.g., 192.168.1.100)
  - Put `HA_URL=http://192.168.1.100:8123` in `.env`
  - Make sure your Mac and the NUC are on the same network

---

## Step 6 — Test the Python agent

```bash
cd /Users/gavin.whitlock/sprinkler_agent
cp .env.example .env
# Edit .env with your real values

pip install -r requirements.txt
python main.py
```

Test HA connectivity:
  Open http://localhost:8000/health in your browser

---

## Step 7 — WhatsApp (Meta Cloud API)

1. Go to developers.facebook.com → Create App → Business
2. Add WhatsApp product
3. Get a test phone number from Meta (or use your own)
4. Under WhatsApp → Configuration → Webhook:
   - URL: `https://<your-ngrok-url>/webhook`
   - Verify token: same as `WHATSAPP_VERIFY_TOKEN` in your `.env`
   - Subscribe to: `messages`
5. Install ngrok: `brew install ngrok` then `ngrok http 8000`
6. Copy the `https://` URL ngrok gives you → paste into Meta webhook URL
7. Click "Verify and Save"

For a permanent URL (not ngrok), deploy to a VPS or use Cloudflare Tunnel.
